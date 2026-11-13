"""Convert Bambu Studio 3MF projects to another printer's presets."""

from __future__ import annotations

import json
import math
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from . import fit, paint
from .printers import Target
from .profiles import ProfileLibrary, settings_only

PROJECT_SETTINGS = "Metadata/project_settings.config"
MODEL_SETTINGS = "Metadata/model_settings.config"
MAIN_MODEL = "3D/3dmodel.model"
MODEL_SETTINGS_RELS = "Metadata/_rels/model_settings.config.rels"
SLICE_INFO = "Metadata/slice_info.config"
CUSTOM_GCODE = "Metadata/custom_gcode_per_print_z.xml"
FILAMENT_SEQUENCE = "Metadata/filament_sequence.json"
GCODE_ENTRY = re.compile(r"^Metadata/plate_\d+\.gcode(\.md5)?$", re.IGNORECASE)

# copied from the source project; speeds/temps come form the target preset
KEEP_PROCESS = (
    # layers and shells
    "layer_height", "wall_loops", "top_shell_layers", "bottom_shell_layers",
    "top_shell_thickness", "bottom_shell_thickness", "wall_generator", "wall_sequence",
    "only_one_wall_top", "only_one_wall_first_layer", "detect_thin_wall",
    "ensure_vertical_shell_thickness", "seam_position", "spiral_mode",
    "xy_hole_compensation", "xy_contour_compensation", "interface_shells",
    # infill
    "sparse_infill_density", "sparse_infill_pattern", "top_surface_pattern",
    "bottom_surface_pattern", "internal_solid_infill_pattern", "infill_direction",
    "infill_combination", "minimum_sparse_infill_area",
    # supports and raft
    "enable_support", "support_type", "support_style", "support_threshold_angle",
    "support_on_build_plate_only", "support_critical_regions_only",
    "support_remove_small_overhang", "support_top_z_distance", "support_bottom_z_distance",
    "support_object_xy_distance", "support_interface_top_layers",
    "support_interface_bottom_layers", "support_interface_pattern",
    "support_interface_spacing", "support_base_pattern", "support_base_pattern_spacing",
    "support_angle", "support_expansion", "independent_support_layer_height",
    "enforce_support_layers", "tree_support_branch_angle", "tree_support_branch_diameter",
    "tree_support_branch_distance", "tree_support_wall_count",
    "raft_layers", "raft_contact_distance", "raft_expansion",
    "raft_first_layer_density", "raft_first_layer_expansion",
    # filament assignment
    "support_filament", "support_interface_filament", "wall_filament",
    "sparse_infill_filament", "solid_infill_filament",
    # adhesion and surface
    "brim_type", "brim_width", "brim_object_gap", "skirt_loops", "skirt_distance",
    "skirt_height", "draft_shield", "ironing_type", "ironing_pattern", "ironing_flow",
    "ironing_spacing", "fuzzy_skin", "fuzzy_skin_thickness", "fuzzy_skin_point_distance",
    "print_sequence",
)

# settings holding a filament number (1-based, 0 = default)
FILAMENT_INDEX_KEYS = ("support_filament", "support_interface_filament", "wall_filament",
                       "sparse_infill_filament", "solid_infill_filament")

# key: (bambu value -> orca value, values orca accepts)
VALUE_NAMES = {
    "ensure_vertical_shell_thickness": (
        {"enabled": "ensure_all", "true": "ensure_all", "1": "ensure_all",
         "partial": "ensure_moderate", "disabled": "none", "false": "none", "0": "none"},
        {"none", "ensure_critical_only", "ensure_moderate", "ensure_all"},
    ),
}

KEEP_IF_SAME_NOZZLE = (
    "line_width", "initial_layer_line_width", "outer_wall_line_width",
    "inner_wall_line_width", "top_surface_line_width", "sparse_infill_line_width",
    "internal_solid_infill_line_width", "support_line_width",
)

_BRAND_WORDS = {"bambu", "lab", "bbl", "generic", "flashforge", "ff", "sunlu", "polymaker",
                "esun", "elegoo", "creality", "overture", "jayo", "kingroon", "cr"}

PLATE_GAP = 1 / 5  # space between plates, fraction of bed size
STANDARD_VARIANT = "Direct Drive Standard"
PER_PLATE = frozenset({"wipe_tower_x", "wipe_tower_y"})
PROJECT_KEYS = frozenset({"version", "from", "curr_bed_type", "filament_map_mode"})


class ConversionError(Exception):
    pass


@dataclass
class SourceFilament:
    index: int          # 1-based, as in the project
    name: str
    type: str
    colour: str
    usage: int = 0      # painted triangles
    used: bool = False


@dataclass
class Plan:
    filaments: list[SourceFilament]
    merge: list[int]                 # toolhead for each source filament
    presets: dict[int, str]          # toolhead -> preset name
    candidates: list[dict]           # filament presets for the target
    notes: dict[int, str] = field(default_factory=dict)

    def toolheads(self) -> list[int]:
        return sorted(set(self.merge))


@dataclass
class SlotMapping:
    slot: int
    colour: str
    source: str
    source_type: str
    target: str
    note: str = ""
    sources: list[int] = field(default_factory=list)


@dataclass
class Report:
    source: Path
    output: Path
    printer: str
    process: str
    slot_word: str = "Toolhead"
    slots: list[SlotMapping] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    moved_objects: int = 0
    repainted: int = 0
    removed_gcode: bool = False
    warnings: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"{self.source.name} -> {self.output.name}",
               f"  Printer: {self.printer}",
               f"  Process: {self.process} (+ {len(self.kept)} settings kept from the project)"]
        for s in self.slots:
            extra = f"  [{s.note}]" if s.note else ""
            if len(s.sources) > 1:
                src = "colours " + " + ".join(map(str, s.sources)) + f" merged ({s.source})"
            else:
                src = f"{s.source} ({s.source_type})"
            out.append(f"  {self.slot_word} {s.slot} {s.colour}: {src} -> {s.target}{extra}")
        if self.repainted:
            out.append(f"  Repainted {self.repainted} triangles for the merged colours")
        if self.moved_objects:
            out.append(f"  Moved {self.moved_objects} object(s) to fit the new bed size")
        if self.removed_gcode:
            out.append("  Removed G-code sliced for the original printer")
        out.extend(f"  Warning: {w}" for w in self.warnings)
        out.extend(f"  {e}" for e in self.extra)
        return out


def _first(value):
    return value[0] if isinstance(value, list) and value else value


def _fit(source_value, target_value):
    if target_value is None:
        return source_value
    if isinstance(target_value, list):
        if isinstance(source_value, list):
            if len(source_value) == len(target_value):
                return source_value
            return [_first(source_value)] * len(target_value)
        return [source_value] * len(target_value)
    return _first(source_value) if isinstance(source_value, list) else source_value


def _as_float(value, default=0.0) -> float:
    try:
        return float(str(_first(value)).rstrip("%"))
    except (TypeError, ValueError):
        return default


def _version(text) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in str(text).split("."))
    except ValueError:
        return (0,)


def _plain_colour(colour: str) -> str:
    # bambu 2.x adds alpha (#RRGGBBAA)
    colour = str(colour).strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{8}", colour):
        return colour[:7].upper()
    return colour.upper() if re.fullmatch(r"#[0-9A-Fa-f]{6}", colour) else "#FFFFFF"


def _rgb(colour: str) -> tuple[int, int, int]:
    c = _plain_colour(colour)
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def colour_distance(a: str, b: str) -> float:
    # "redmean" approximation of perceived difference
    (r1, g1, b1), (r2, g2, b2) = _rgb(a), _rgb(b)
    rm = (r1 + r2) / 2
    return math.sqrt((2 + rm / 256) * (r1 - r2) ** 2 + 4 * (g1 - g2) ** 2
                     + (2 + (255 - rm) / 256) * (b1 - b2) ** 2)


def _tokens(name: str) -> set[str]:
    base = name.split("@")[0]
    words = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", base.lower())
    return {w for w in words if w not in _BRAND_WORDS}


_TYPE_ALIASES = {"SILK": "PLA", "PLA-SILK": "PLA", "FLEX": "TPU"}  # flashforge uses SILK


def _family(filament_type: str) -> str:
    t = filament_type.upper().strip()
    return _TYPE_ALIASES.get(t, t.split("-")[0].split(" ")[0])


def _bed_box(printable_area) -> tuple[float, float, float, float]:
    if isinstance(printable_area, str):
        printable_area = printable_area.split(",")
    xs, ys = [], []
    for point in printable_area or []:
        x, _, y = str(point).partition("x")
        xs.append(float(x))
        ys.append(float(y))
    if not xs:
        raise ConversionError("The project has no bed size (printable_area).")
    return min(xs), min(ys), max(xs), max(ys)


def _plate_columns(count: int) -> int:
    # same as orca's compute_colum_count
    value = math.sqrt(count)
    rounded = round(value)
    return rounded + 1 if value > rounded else rounded


def _plate_centre(index: int, columns: int, box) -> tuple[float, float]:
    x0, y0, x1, y1 = box
    width, depth = x1 - x0, y1 - y0
    row, col = divmod(index, columns)
    origin_x = col * width * (1 + PLATE_GAP)
    origin_y = -row * depth * (1 + PLATE_GAP)
    return origin_x + (x0 + x1) / 2, origin_y + (y0 + y1) / 2


def choose_process(lib: ProfileLibrary, target: Target, layer_height: float) -> dict:
    candidates = lib.compatible("process", target.machine)
    if not candidates:
        return lib.resolve("process", target.default_process)

    def rank(p):
        return (abs(_as_float(p.get("layer_height"), 0.2) - layer_height),
                p["name"] != target.default_process,
                "standard" not in p["name"].lower(),
                p["name"])
    return min(candidates, key=rank)


def choose_filament(candidates: list[dict], source_name: str, source_type: str) -> tuple[dict, str]:
    def ftype(p):
        return str(_first(p.get("filament_type")) or "").upper()

    candidates = [p for p in candidates if not p.get("_user")]  # auto picks system presets only
    wanted = source_type.upper()
    note = ""
    same = [p for p in candidates if _family(ftype(p)) == _family(wanted)]
    if not same:
        same = [p for p in candidates if ftype(p) == "PLA"]
        note = f"no {source_type} preset for this printer, used PLA: check it"
    if not same:
        raise ConversionError("The target printer has no filament presets installed.")

    want = _tokens(source_name) | {wanted.lower()}

    def score(p):
        have = _tokens(p["name"])
        return (len(want & have) - 0.1 * len(have - want)
                + (0.5 if ftype(p) == wanted else 0.0)
                + (0.3 if p["name"].lower().startswith("generic") else 0.0))
    best = max(same, key=lambda p: (score(p), -len(p["name"]), p["name"]))
    return best, note


def auto_merge(filaments: list[SourceFilament], toolheads: int) -> list[int]:
    """toolhead for each source filament; merges the closest colours when there are too many"""
    if len(filaments) <= toolheads:
        return [f.index for f in filaments]
    used = [f for f in filaments if f.used] or filaments[:1]
    groups = [[f] for f in used]
    while len(groups) > toolheads:
        best = None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                d = colour_distance(_lead(groups[i]).colour, _lead(groups[j]).colour)
                if best is None or d < best[0]:
                    best = (d, i, j)
        _, i, j = best
        groups[i] += groups.pop(j)
    groups.sort(key=lambda g: min(f.index for f in g))
    head = {}
    for t, g in enumerate(groups, start=1):
        for f in g:
            head[f.index] = t
    for f in filaments:
        if f.index not in head:
            nearest = min(range(len(groups)),
                          key=lambda k: colour_distance(f.colour, _lead(groups[k]).colour))
            head[f.index] = nearest + 1
    return [head[f.index] for f in filaments]


def _lead(group: list[SourceFilament]) -> SourceFilament:
    return max(group, key=lambda f: (f.usage, -f.index))


def _read_project(zin: zipfile.ZipFile, name: str) -> dict:
    entries = set(zin.namelist())
    if PROJECT_SETTINGS not in entries:
        raise ConversionError(
            f"{name} has no slicer project settings, so it is a bare model. "
            "Open it in your slicer directly; there is nothing to convert.")
    try:
        return json.loads(zin.read(PROJECT_SETTINGS).decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ConversionError(f"The project settings in {name} are damaged ({exc}).") from None


def _model_entries(zin: zipfile.ZipFile) -> list[str]:
    return [n for n in zin.namelist() if n.startswith("3D/") and n.endswith(".model")]


_EXTRUDER_META = re.compile(
    r'(<metadata\b[^>]*key="(?:extruder|' + "|".join(FILAMENT_INDEX_KEYS) + r')"[^>]*value=")(\d+)(")')


def analyse(source: Path, target: Target, lib: ProfileLibrary) -> Plan:
    source = Path(source)
    try:
        zin = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ConversionError(f"{source.name} is not a readable 3MF file ({exc}).") from None
    with zin:
        settings = _read_project(zin, source.name)
        names = settings.get("filament_settings_id") or []
        colours = settings.get("filament_colour") or []
        types = settings.get("filament_type") or []
        count = max(len(names), len(colours), len(types), 1)
        filaments = [SourceFilament(i + 1, names[i] if i < len(names) else "",
                                    types[i] if i < len(types) else "PLA",
                                    _plain_colour(colours[i] if i < len(colours) else "#FFFFFF"))
                     for i in range(count)]
        usage: dict[int, int] = {}
        for entry in _model_entries(zin):
            for state, n in paint.used_states(zin.read(entry).decode("utf-8", "replace")).items():
                usage[state] = usage.get(state, 0) + n
        if MODEL_SETTINGS in zin.namelist():
            for m in _EXTRUDER_META.finditer(zin.read(MODEL_SETTINGS).decode("utf-8", "replace")):
                usage.setdefault(int(m.group(2)), 0)
    for key in FILAMENT_INDEX_KEYS:
        n = int(_as_float(settings.get(key), 0))
        if n:
            usage.setdefault(n, 0)
    for f in filaments:
        if f.index in usage:
            f.used = True
            f.usage = usage[f.index]
    merge = auto_merge(filaments, target.toolheads)
    candidates = lib.compatible("filament", target.machine, include_user=True)
    plan = Plan(filaments, merge, {}, candidates)
    fill_presets(plan)
    return plan


def suggest_preset(plan: Plan, merge: list[int], toolhead: int) -> str:
    group = [f for f, h in zip(plan.filaments, merge) if h == toolhead]
    if not group:
        return ""
    lead = _lead(group)
    return choose_filament(plan.candidates, lead.name, lead.type)[0]["name"]


def lead_colour(plan: Plan, merge: list[int], toolhead: int) -> str:
    group = [f for f, h in zip(plan.filaments, merge) if h == toolhead]
    return _lead(group).colour if group else "#FFFFFF"


def fill_presets(plan: Plan) -> None:
    """auto-pick a preset for any toolhead that doesn't have one"""
    for t in plan.toolheads():
        if t not in plan.presets:
            lead = _lead([f for f, h in zip(plan.filaments, plan.merge) if h == t])
            profile, note = choose_filament(plan.candidates, lead.name, lead.type)
            plan.presets[t] = profile["name"]
            plan.notes[t] = note


def _rewrite_settings(src: dict, lib: ProfileLibrary, target: Target, overrides: dict[int, str],
                      plan: Plan, report: Report, colours: dict[int, str] | None = None) -> dict:
    sources = plan.filaments
    merge = plan.merge
    if max(merge) > target.toolheads or min(merge) < 1:
        raise ConversionError(f"Toolheads must be between 1 and {target.toolheads}.")
    count = max(merge)
    n_src = len(sources)

    machine = lib.resolve("machine", target.machine)
    layer_height = _as_float(src.get("layer_height"), 0.2)
    process = choose_process(lib, target, layer_height)
    report.printer = machine["name"]
    report.process = process["name"]
    report.slot_word = "Toolhead" if target.toolheads > 1 else "Filament"

    candidates = plan.candidates
    reps: list[int] = []        # source index (0-based) that stands for each toolhead
    chosen = []
    for t in range(1, count + 1):
        group = [f for f, h in zip(sources, merge) if h == t]
        if group:
            lead = _lead(group)
            reps.append(lead.index - 1)
        else:
            lead = SourceFilament(0, "", "PLA", "#FFFFFF")
            reps.append(0)
        name = overrides.get(t) or plan.presets.get(t)
        note = plan.notes.get(t, "")
        if name:
            profile = lib.resolve("filament", name)
            if overrides.get(t):
                note = "chosen by you"
        else:
            profile, note = choose_filament(candidates, lead.name, lead.type)
        if profile.get("_user"):
            note = f"your preset, from {profile['_user']}"
        if not group:
            note = "unused"
        colour = lead.colour
        new = _plain_colour(colours[t]) if colours and colours.get(t) else colour
        if new != colour:
            note = "; ".join(x for x in (note, f"colour was {colour}") if x)
            colour = new
        chosen.append(profile)
        report.slots.append(SlotMapping(t, colour, lead.name or "(unnamed)", lead.type,
                                        profile["name"], note, [f.index for f in group]))

    out = dict(src)
    out.update(settings_only(machine))
    process_settings = settings_only(process)
    out.update(process_settings)

    src_nozzle = _as_float(src.get("nozzle_diameter"), 0.4)
    dst_nozzle = _as_float(machine.get("nozzle_diameter"), 0.4)
    keep = list(KEEP_PROCESS)
    if abs(src_nozzle - dst_nozzle) < 1e-6:
        keep += KEEP_IF_SAME_NOZZLE
    changed = []
    for key in keep:
        if key not in src:
            continue
        value = _fit(src[key], process_settings.get(key))
        if key in VALUE_NAMES:
            renames, allowed = VALUE_NAMES[key]
            value = renames.get(str(value).lower(), value)
            usable = value in allowed
        elif key in FILAMENT_INDEX_KEYS:
            n = int(_as_float(value, 0))
            value = str(merge[n - 1]) if 0 < n <= n_src else "0"
            usable = True
        else:
            # -1 means auto in bambu, orca wants >= 0
            usable = not (_as_float(src[key], 0.0) < 0 <= _as_float(process_settings.get(key), 0.0))
        if not usable:
            if key in process_settings:
                out[key] = process_settings[key]
            else:
                out.pop(key, None)
            continue
        out[key] = value
        if key in process_settings and value != process_settings[key]:
            changed.append(key)
    report.kept = changed

    filament_settings = [settings_only(p) for p in chosen]
    for key in sorted(set().union(*filament_settings)):
        values = []
        for i, fs in enumerate(filament_settings):
            if key in fs:
                values.append(_first(fs[key]))
            else:
                fallback = src.get(key)
                r = reps[i]
                values.append(fallback[r] if isinstance(fallback, list) and r < len(fallback)
                              else _first(fallback) if fallback is not None else "")
        out[key] = values
    covered = (set(settings_only(machine)) | set(process_settings)
               | set().union(*filament_settings) | set(keep))
    _tidy_leftovers(out, src, covered, n_src, reps)
    # needs one entry per toolhead, slicing fails otherwise
    if isinstance(machine.get("default_nozzle_volume_type"), list):
        out["nozzle_volume_type"] = list(machine["default_nozzle_volume_type"])

    out["filament_colour"] = [s.colour for s in report.slots]
    out["filament_settings_id"] = [p["name"] for p in chosen]
    out["filament_ids"] = [p.get("filament_id", "") for p in chosen]
    out["filament_map"] = ["1"] * count

    out["printer_settings_id"] = machine["name"]
    out["print_settings_id"] = process["name"]
    out["print_compatible_printers"] = process.get("compatible_printers", [machine["name"]])
    out["inherits_group"] = [""] + [p.get("_parent", "") for p in chosen] + [""]
    out["different_settings_to_system"] = [";".join(sorted(changed))] + [""] * (count + 1)
    out.pop("inherits", None)

    try:
        model = lib.resolve("machine", machine.get("printer_model", ""))
        if model.get("default_bed_type"):
            out["curr_bed_type"] = model["default_bed_type"]
    except KeyError:
        pass

    min_lh = _as_float(machine.get("min_layer_height"), 0.0)
    max_lh = _as_float(machine.get("max_layer_height"), 99.0)
    if not min_lh <= layer_height <= max_lh:
        report.warnings.append(
            f"layer height {layer_height} mm is outside this printer's {min_lh}-{max_lh} mm range")
    if abs(src_nozzle - dst_nozzle) >= 1e-6:
        report.warnings.append(
            f"project was set up for a {src_nozzle} mm nozzle; line widths now use the "
            f"{dst_nozzle} mm defaults")
    return out


def _tidy_leftovers(out: dict, src: dict, covered: set, n_src: int, reps: list[int]) -> None:
    # bambu 2.x has per-nozzle-type filament lists (filaments x variants).
    # keep the standard nozzle entry, drop other lists that don't fit.
    variants = src.get("filament_extruder_variant") or []
    owners = src.get("filament_self_index") or []
    pick: list[int] | None = None
    if len(variants) > n_src and len(owners) == len(variants):
        pick = []
        for slot in range(1, n_src + 1):
            rows = [i for i, owner in enumerate(owners) if str(owner) == str(slot)]
            standard = [i for i in rows if variants[i] == STANDARD_VARIANT]
            if not rows:
                pick = None
                break
            pick.append((standard or rows)[0])

    for key, value in src.items():
        if key in covered or key in PER_PLATE:
            continue
        # anything else single-valued from the source printer goes; the slicer
        # fills in its own default. bambu-only options (e.g. support_object_skip_flush)
        # break slicing on other printers otherwise
        if not isinstance(value, list):
            if key not in PROJECT_KEYS:
                out.pop(key, None)
            continue
        n = len(value)
        if n == n_src:
            out[key] = [value[r] for r in reps]
        elif n in (0, 1):
            continue
        elif n == n_src * n_src:
            out[key] = [value[a * n_src + b] for a in reps for b in reps]
        elif pick is not None and n == len(variants):
            out[key] = [value[pick[r]] for r in reps]
        else:
            out.pop(key, None)
    if pick is not None or "filament_self_index" in out:
        out["filament_self_index"] = [str(i) for i in range(1, len(reps) + 1)]


def _plate_of_instances(model_settings: str | None) -> tuple[dict[tuple[str, str], int], int]:
    if not model_settings:
        return {}, 1
    root = ET.fromstring(model_settings)
    plates = root.findall("plate")
    where = {}
    for index, plate in enumerate(plates):
        meta = {m.get("key"): m.get("value") for m in plate.findall("metadata")}
        plate_index = int(meta.get("plater_id", index + 1)) - 1
        for inst in plate.findall("model_instance"):
            imeta = {m.get("key"): m.get("value") for m in inst.findall("metadata")}
            where[(imeta.get("object_id", ""), imeta.get("instance_id", "0"))] = plate_index
    return where, max(len(plates), 1)


def _object_names(model_settings: str | None) -> dict[str, str]:
    if not model_settings:
        return {}
    names = {}
    for obj in ET.fromstring(model_settings).findall("object"):
        for m in obj.findall("metadata"):
            if m.get("key") == "name":
                names[obj.get("id", "")] = m.get("value", "")
    return names


_ITEM = re.compile(r"<item\b[^>]*>")
_ATTR = re.compile(r'(\w+)="([^"]*)"')


def _move_build_items(model_xml: str, plate_of: dict, plate_count: int,
                      old_box, new_box) -> tuple[str, int]:
    columns = _plate_columns(plate_count)
    seen: dict[str, int] = {}
    moved = 0

    def shift(match: re.Match) -> str:
        nonlocal moved
        tag = match.group(0)
        attrs = dict(_ATTR.findall(tag))
        obj = attrs.get("objectid", "")
        instance = seen.get(obj, 0)
        seen[obj] = instance + 1
        transform = attrs.get("transform")
        if not transform:
            return tag
        plate = plate_of.get((obj, str(instance)), 0)
        ox, oy = _plate_centre(plate, columns, old_box)
        nx, ny = _plate_centre(plate, columns, new_box)
        numbers = transform.split()
        if len(numbers) != 12 or (abs(nx - ox) < 1e-9 and abs(ny - oy) < 1e-9):
            return tag
        numbers[9] = repr(float(numbers[9]) + nx - ox)
        numbers[10] = repr(float(numbers[10]) + ny - oy)
        moved += 1
        return tag.replace(f'transform="{transform}"', f'transform="{" ".join(numbers)}"')

    return _ITEM.sub(shift, model_xml), moved


def _label_model(model_xml: str, target: Target) -> str:
    model_xml = re.sub(r'(<metadata name="Application">BambuStudio-)[\d.]+(</metadata>)',
                       rf"\g<1>{target.file_version}\2", model_xml, count=1)
    orca = f'<metadata name="OrcaSlicer">{target.orca_version}</metadata>'
    if re.search(r'<metadata name="OrcaSlicer">', model_xml):
        return re.sub(r'<metadata name="OrcaSlicer">[^<]*</metadata>', orca, model_xml, count=1)
    return re.sub(r'(<metadata name="Application">[^<]*</metadata>)', rf"\1\n {orca}", model_xml, count=1)


def _move_prime_towers(settings: dict, old_box, new_box) -> None:
    tower = _as_float(settings.get("prime_tower_width"), 35.0)
    for key, lo, hi in (("wipe_tower_x", 0, 2), ("wipe_tower_y", 1, 3)):
        values = settings.get(key)
        if not isinstance(values, list):
            continue
        old_lo, old_hi, new_lo, new_hi = old_box[lo], old_box[hi], new_box[lo], new_box[hi]
        moved = []
        for v in values:
            ratio = (_as_float(v) - old_lo) / ((old_hi - old_lo) or 1)
            pos = new_lo + ratio * (new_hi - new_lo)
            pos = min(max(pos, new_lo), max(new_lo, new_hi - tower))
            moved.append(f"{pos:.3f}".rstrip("0").rstrip("."))
        settings[key] = moved


def _drop_gcode_rels(rels_xml: str) -> str | None:
    kept = [r for r in re.findall(r"<Relationship\b[^>]*/>", rels_xml)
            if not re.search(r'Target="[^"]*\.gcode"', r, re.IGNORECASE)]
    if not kept:
        return None
    head = rels_xml[:rels_xml.find("<Relationship")]
    return head + "\n ".join(kept) + "\n</Relationships>\n"


def convert(source: Path, output: Path, target: Target, lib: ProfileLibrary,
            overrides: dict[int, str] | None = None, merge: list[int] | None = None,
            plan: Plan | None = None, colours: dict[int, str] | None = None) -> Report:
    """colours: toolhead -> #RRGGBB to show instead of the designer's colour"""
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve():
        raise ConversionError("The output would overwrite the original file.")
    try:
        zin = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ConversionError(f"{source.name} is not a readable 3MF file ({exc}).") from None

    with zin:
        entries = {i.filename: i for i in zin.infolist()}
        src_settings = _read_project(zin, source.name)
        if src_settings.get("printer_settings_id") == target.machine:
            raise ConversionError(
                f"{source.name} is already set up for the {target.label}. "
                "Open it in your slicer directly; there is nothing to convert.")

        plan = plan or analyse(source, target, lib)
        if merge is not None:
            if len(merge) != len(plan.filaments):
                raise ConversionError(
                    f"The project has {len(plan.filaments)} filaments; give a toolhead for each.")
            plan.merge = list(merge)
            fill_presets(plan)
        if max(plan.merge) > target.toolheads:
            raise ConversionError(
                f"This project uses {len(plan.filaments)} filaments and the {target.label} has "
                f"{target.toolheads}. Choose which toolhead each colour goes to.")

        report = Report(source, output, "", "")
        bad = [c for c in (colours or {}).values() if not re.fullmatch(r"#?[0-9A-Fa-f]{6}", str(c).strip())]
        if bad:
            raise ConversionError(f"{bad[0]} isn't a colour; use #RRGGBB, e.g. #2E7D32.")
        colours = {t: c if str(c).startswith("#") else "#" + str(c) for t, c in (colours or {}).items()}
        new_settings = _rewrite_settings(src_settings, lib, target, overrides or {}, plan, report, colours)
        mapping = {f.index: h for f, h in zip(plan.filaments, plan.merge)}
        remap = any(k != v for k, v in mapping.items())
        merged = len(set(plan.merge)) < len(plan.filaments)
        if target.toolheads == 1 and len(plan.filaments) > 1:
            report.warnings.append("all colours print in one filament on this printer")

        replacements: dict[str, bytes | None] = {}
        model_settings = (zin.read(MODEL_SETTINGS).decode("utf-8")
                          if MODEL_SETTINGS in entries else None)
        old_box = _bed_box(src_settings.get("printable_area"))
        new_box = _bed_box(new_settings.get("printable_area"))
        newer = _version(src_settings.get("version")) > _version(target.file_version)

        models = {name: zin.read(name).decode("utf-8") for name in _model_entries(zin)}
        touched: set[str] = set()

        if remap:
            for name, xml in models.items():
                new_xml, n = paint.remap_model(xml, mapping)
                if n:
                    models[name] = new_xml
                    touched.add(name)
                    report.repainted += n
            if model_settings is not None:
                model_settings = _EXTRUDER_META.sub(
                    lambda m: m.group(1) + str(mapping.get(int(m.group(2)), int(m.group(2)))) + m.group(3),
                    model_settings)
                model_settings = re.sub(
                    r'(<metadata key="filament_maps" value=")[^"]*(")',
                    lambda m: m.group(1) + " ".join(str(i) for i in range(1, max(plan.merge) + 1)) + m.group(2),
                    model_settings)
                replacements[MODEL_SETTINGS] = b""  # filled in below
            if CUSTOM_GCODE in entries:
                xml = zin.read(CUSTOM_GCODE).decode("utf-8")
                xml = re.sub(r'(\bextruder=")(\d+)(")',
                             lambda m: m.group(1) + str(mapping.get(int(m.group(2)), int(m.group(2)))) + m.group(3),
                             xml)
                replacements[CUSTOM_GCODE] = xml.encode("utf-8")
            if merged and FILAMENT_SEQUENCE in entries:
                try:
                    seq = json.loads(zin.read(FILAMENT_SEQUENCE))
                    for plate in seq.values():
                        if isinstance(plate, dict):
                            for k in plate:
                                plate[k] = []
                    replacements[FILAMENT_SEQUENCE] = json.dumps(seq).encode("utf-8")
                except (ValueError, AttributeError):
                    replacements[FILAMENT_SEQUENCE] = None

        plate_of, plate_count = _plate_of_instances(model_settings)
        if old_box != new_box:
            _move_prime_towers(new_settings, old_box, new_box)
            if MAIN_MODEL in models:
                models[MAIN_MODEL], report.moved_objects = _move_build_items(
                    models[MAIN_MODEL], plate_of, plate_count, old_box, new_box)
                touched.add(MAIN_MODEL)

        if newer:
            # flash studio refuses files from newer bambu versions
            new_settings["version"] = target.file_version
            if MAIN_MODEL in models:
                models[MAIN_MODEL] = _label_model(models[MAIN_MODEL], target)
                touched.add(MAIN_MODEL)
            if SLICE_INFO in entries:
                info = zin.read(SLICE_INFO).decode("utf-8")
                info = re.sub(r'(key="X-BBL-Client-Version" value=")[^"]*(")',
                              rf"\g<1>{target.file_version}\2", info)
                if "OrcaSlicer-Version" not in info:
                    info = re.sub(r'(<header_item key="X-BBL-Client-Version"[^>]*/>)',
                                  rf'\1\n    <header_item key="OrcaSlicer-Version" value="{target.orca_version}"/>',
                                  info)
                replacements[SLICE_INFO] = info.encode("utf-8")

        if MAIN_MODEL in models:
            columns = _plate_columns(plate_count)
            try:
                report.warnings += fit.check(
                    models, MAIN_MODEL, plate_of,
                    lambda i: _plate_centre(i, columns, new_box), new_box,
                    _as_float(new_settings.get("printable_height"), 0.0),
                    _object_names(model_settings))
            except (ValueError, KeyError, IndexError):
                pass

        replacements[PROJECT_SETTINGS] = json.dumps(new_settings, indent=4, ensure_ascii=False).encode("utf-8")
        for name in touched:
            replacements[name] = models[name].encode("utf-8")

        for name in entries:
            if GCODE_ENTRY.match(name):
                replacements[name] = None
                report.removed_gcode = True
        if report.removed_gcode and model_settings is not None:
            model_settings = re.sub(r'(<metadata key="gcode_file" value=")[^"]*(")', r"\1\2", model_settings)
            replacements[MODEL_SETTINGS] = b""
            if MODEL_SETTINGS_RELS in entries:
                rels = _drop_gcode_rels(zin.read(MODEL_SETTINGS_RELS).decode("utf-8"))
                replacements[MODEL_SETTINGS_RELS] = rels.encode("utf-8") if rels else None
        if MODEL_SETTINGS in replacements:
            replacements[MODEL_SETTINGS] = model_settings.encode("utf-8")

        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_name(output.name + ".partial")
        try:
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zout:
                for info in zin.infolist():
                    if info.filename in replacements:
                        data = replacements[info.filename]
                        if data is not None:
                            zout.writestr(_fresh_info(info), data)
                        continue
                    with zin.open(info) as src_fh, zout.open(_fresh_info(info), "w", force_zip64=True) as dst_fh:
                        shutil.copyfileobj(src_fh, dst_fh, 1024 * 1024)
            partial.replace(output)
        finally:
            if partial.exists():
                partial.unlink()
    return report


def _fresh_info(info: zipfile.ZipInfo) -> zipfile.ZipInfo:
    fresh = zipfile.ZipInfo(info.filename, date_time=info.date_time)
    fresh.compress_type = zipfile.ZIP_DEFLATED
    fresh.external_attr = info.external_attr
    return fresh


def default_output(source: Path, target: Target) -> Path:
    source = Path(source)
    return source.with_name(f"{source.stem} - {target.suffix}.3mf")
