"""Convert Bambu Studio 3MF projects to another printer's presets."""

from __future__ import annotations

import dataclasses
import json
import math
import re
import shutil
import zipfile
import zlib
from contextlib import contextmanager
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

# real projects are tens of mb; these only stop zip bombs
MAX_ENTRY_BYTES = 2_000_000_000
MAX_TOTAL_BYTES = 4_000_000_000

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


# anything a damaged or unusual file can make the parsing code raise
_PARSE_ERRORS = (ValueError, KeyError, IndexError, TypeError, AttributeError, RecursionError,
                 ET.ParseError, UnicodeDecodeError, zipfile.BadZipFile, zlib.error,
                 OverflowError, EOFError)


@contextmanager
def _plain_errors(name: str):
    """turn whatever a bad file causes into one ConversionError with a readable message"""
    try:
        yield
    except ConversionError:
        raise
    except OSError as exc:
        raise ConversionError(f"{name}: {exc.strerror or exc}") from exc
    except _PARSE_ERRORS as exc:
        detail = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
        raise ConversionError(
            f"{name} couldn't be converted; it looks damaged or unusual "
            f"({type(exc).__name__}: {detail}).") from exc


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


# --- small helpers ---------------------------------------------------------

def _first(value):
    return value[0] if isinstance(value, list) and value else value


def _as_list(value) -> list:
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


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
        number = float(str(_first(value)).rstrip("%"))
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


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
        try:
            xs.append(float(x))
            ys.append(float(y))
        except ValueError:
            raise ConversionError(f"The bed size in the project is unreadable ({point!r}).") from None
    if not xs or not all(math.isfinite(v) for v in xs + ys):
        raise ConversionError("The project has no usable bed size (printable_area).")
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


def default_output(source: Path, target: Target) -> Path:
    source = Path(source)
    return source.with_name(f"{source.stem} - {target.suffix}.3mf")


def unique_output(path: Path) -> Path:
    """path, or 'name (2).3mf', 'name (3).3mf'... if it's taken"""
    path = Path(path)
    n = 2
    candidate = path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


# --- choosing presets ------------------------------------------------------

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
    wanted = str(source_type).upper()
    note = ""
    same = [p for p in candidates if _family(ftype(p)) == _family(wanted)]
    if not same:
        same = [p for p in candidates if ftype(p) == "PLA"]
        note = f"no {source_type} preset for this printer, used PLA: check it"
    if not same:
        raise ConversionError("The target printer has no filament presets installed.")

    want = _tokens(str(source_name)) | {wanted.lower()}

    def score(p):
        have = _tokens(p["name"])
        return (len(want & have) - 0.1 * len(have - want)
                + (0.5 if ftype(p) == wanted else 0.0)
                + (0.3 if p["name"].lower().startswith("generic") else 0.0)
                # tuned for this printer beats the brand's printer-neutral version
                + (1.0 if p.get("_explicit") else 0.0))
    best = max(same, key=lambda p: (score(p), -len(p["name"]), p["name"]))
    return best, note


def _lead(group: list[SourceFilament]) -> SourceFilament:
    return max(group, key=lambda f: (f.usage, -f.index))


def _group(plan: Plan, merge: list[int], toolhead: int) -> list[SourceFilament]:
    return [f for f, h in zip(plan.filaments, merge, strict=True) if h == toolhead]


def auto_merge(filaments: list[SourceFilament], toolheads: int) -> list[int]:
    """toolhead for each source filament; merges the closest colours when there are too many"""
    if len(filaments) <= toolheads:
        return [f.index for f in filaments]
    used = [f for f in filaments if f.used] or filaments[:1]
    groups = [[f] for f in used]
    while len(groups) > toolheads:
        pairs = ((colour_distance(_lead(groups[i]).colour, _lead(groups[j]).colour), i, j)
                 for i in range(len(groups)) for j in range(i + 1, len(groups)))
        _, i, j = min(pairs)
        groups[i] += groups.pop(j)
    groups.sort(key=lambda g: min(f.index for f in g))
    head = {f.index: t for t, g in enumerate(groups, start=1) for f in g}
    for f in filaments:
        if f.index not in head:
            nearest = min(range(len(groups)),
                          key=lambda k: colour_distance(f.colour, _lead(groups[k]).colour))
            head[f.index] = nearest + 1
    return [head[f.index] for f in filaments]


def suggest(plan: Plan, merge: list[int], toolhead: int) -> tuple[str, str]:
    """(preset name, note) the automatic choice gives a toolhead under this merge"""
    group = _group(plan, merge, toolhead)
    if not group:
        return "", ""
    lead = _lead(group)
    profile, note = choose_filament(plan.candidates, lead.name, lead.type)
    return profile["name"], note


def suggest_preset(plan: Plan, merge: list[int], toolhead: int) -> str:
    return suggest(plan, merge, toolhead)[0]


def lead_colour(plan: Plan, merge: list[int], toolhead: int) -> str:
    group = _group(plan, merge, toolhead)
    return _lead(group).colour if group else "#FFFFFF"


def fill_presets(plan: Plan) -> None:
    """auto-pick a preset for any toolhead that doesn't have one"""
    for t in plan.toolheads():
        if t not in plan.presets:
            plan.presets[t], plan.notes[t] = suggest(plan, plan.merge, t)


def with_merge(plan: Plan, merge: list[int] | None) -> Plan:
    """the plan for another toolhead layout. presets chosen for the old layout
    belong to other colours now, so they're picked again (never edits `plan`)"""
    if merge is None or list(merge) == plan.merge:
        return plan
    if len(merge) != len(plan.filaments):
        raise ConversionError(f"The project has {len(plan.filaments)} filaments; give a toolhead for each.")
    new = dataclasses.replace(plan, merge=list(merge), presets={}, notes={})
    fill_presets(new)
    return new


# --- reading a project -----------------------------------------------------

def _open(source: Path) -> zipfile.ZipFile:
    try:
        zin = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ConversionError(f"{source.name} is not a readable 3MF file ({exc}).") from None
    total = 0
    for info in zin.infolist():
        total += info.file_size
        if info.file_size > MAX_ENTRY_BYTES or total > MAX_TOTAL_BYTES:
            zin.close()
            raise ConversionError(f"{source.name} unpacks to an implausible size; not converting it.")
    return zin


def _read_project(zin: zipfile.ZipFile, name: str) -> dict:
    if PROJECT_SETTINGS not in set(zin.namelist()):
        raise ConversionError(
            f"{name} has no slicer project settings, so it is a bare model. "
            "Open it in your slicer directly; there is nothing to convert.")
    try:
        settings = json.loads(zin.read(PROJECT_SETTINGS).decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ConversionError(f"The project settings in {name} are damaged ({exc}).") from None
    if not isinstance(settings, dict):
        raise ConversionError(f"The project settings in {name} aren't in the expected form.")
    return settings


def _model_entries(zin: zipfile.ZipFile) -> list[str]:
    return [n for n in zin.namelist() if n.startswith("3D/") and n.endswith(".model")]


_EXTRUDER_META = re.compile(
    r'(<metadata\b[^>]*key="(?:extruder|' + "|".join(FILAMENT_INDEX_KEYS) + r')"[^>]*value=")(\d+)(")')


def _source_filaments(settings: dict) -> list[SourceFilament]:
    names = _as_list(settings.get("filament_settings_id"))
    colours = _as_list(settings.get("filament_colour"))
    types = _as_list(settings.get("filament_type"))
    count = max(len(names), len(colours), len(types), 1)
    return [SourceFilament(i + 1, str(names[i]) if i < len(names) else "",
                           str(types[i]) if i < len(types) else "PLA",
                           _plain_colour(colours[i] if i < len(colours) else "#FFFFFF"))
            for i in range(count)]


def _usage(zin: zipfile.ZipFile, settings: dict) -> dict[int, int]:
    """painted triangles per filament; filaments used some other way count as 0"""
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
    return usage


def analyse(source: Path, target: Target, lib: ProfileLibrary) -> Plan:
    source = Path(source)
    with _plain_errors(source.name):
        with _open(source) as zin:
            settings = _read_project(zin, source.name)
            filaments = _source_filaments(settings)
            usage = _usage(zin, settings)
        for f in filaments:
            if f.index in usage:
                f.used = True
                f.usage = usage[f.index]
        candidates = lib.compatible("filament", target.machine, include_user=True)
        plan = Plan(filaments, auto_merge(filaments, target.toolheads), {}, candidates)
        fill_presets(plan)
        return plan


# --- the new project settings ----------------------------------------------

def _slots(plan: Plan, target: Target, lib: ProfileLibrary, overrides: dict[int, str],
           colours: dict[int, str], report: Report) -> tuple[list[dict], list[int]]:
    """the preset for each toolhead, and which source filament stands for it"""
    chosen, reps = [], []
    for t in range(1, max(plan.merge) + 1):
        group = _group(plan, plan.merge, t)
        lead = _lead(group) if group else SourceFilament(0, "", "PLA", "#FFFFFF")
        reps.append(lead.index - 1 if group else 0)
        note = plan.notes.get(t, "")
        if overrides.get(t):
            profile, note = lib.resolve("filament", overrides[t]), "chosen by you"
        elif plan.presets.get(t):
            profile = lib.resolve("filament", plan.presets[t])
        else:
            profile, note = choose_filament(plan.candidates, lead.name, lead.type)
        if profile.get("_user"):
            note = f"your preset, from {profile['_user']}"
        if not group:
            note = "unused"
        colour = lead.colour
        new = _plain_colour(colours[t]) if colours.get(t) else colour
        if new != colour:
            note = "; ".join(x for x in (note, f"colour was {colour}") if x)
            colour = new
        chosen.append(profile)
        report.slots.append(SlotMapping(t, colour, lead.name or "(unnamed)", lead.type,
                                        profile["name"], note, [f.index for f in group]))
    return chosen, reps


def _temperature_clash(filaments: list[dict]) -> str | None:
    """'PLA (210°C) and PETG (240°C)' when one filament's printing temperature is
    outside another's recommended range; slicers refuse to mix those on one nozzle"""
    def temps(f):
        return (_as_float(f.get("nozzle_temperature"), 0), _as_float(f.get("nozzle_temperature_range_low"), 0),
                _as_float(f.get("nozzle_temperature_range_high"), 999))
    for i, a in enumerate(filaments):
        for b in filaments[i + 1:]:
            (ta, lo_a, hi_a), (tb, lo_b, hi_b) = temps(a), temps(b)
            if not (ta and tb):
                continue
            if not (lo_b <= ta <= hi_b and lo_a <= tb <= hi_a):
                return (f"{_first(a.get('filament_type'))} ({ta:.0f}°C) and "
                        f"{_first(b.get('filament_type'))} ({tb:.0f}°C)")
    return None


def _carry_over(src: dict, out: dict, process: dict, keep: list[str], merge: list[int]) -> list[str]:
    """copy the designer's choices over the target process; returns what differs from it"""
    changed = []
    for key in keep:
        if key not in src:
            continue
        value = _fit(src[key], process.get(key))
        if key in VALUE_NAMES:
            renames, allowed = VALUE_NAMES[key]
            value = renames.get(str(value).lower(), value)
            usable = value in allowed
        elif key in FILAMENT_INDEX_KEYS:
            n = int(_as_float(value, 0))
            value = str(merge[n - 1]) if 0 < n <= len(merge) else "0"
            usable = True
        else:
            # -1 means auto in bambu, orca wants >= 0
            usable = not (_as_float(src[key], 0.0) < 0 <= _as_float(process.get(key), 0.0))
        if not usable:
            if key in process:
                out[key] = process[key]
            else:
                out.pop(key, None)
            continue
        out[key] = value
        if key in process and value != process[key]:
            changed.append(key)
    return changed


def _filament_lists(out: dict, src: dict, chosen: list[dict], reps: list[int]) -> list[dict]:
    """one entry per toolhead for every setting the chosen presets define"""
    settings = [settings_only(p) for p in chosen]
    for key in sorted(set().union(*settings)):
        values = []
        for fs, r in zip(settings, reps, strict=True):
            if key in fs:
                values.append(_first(fs[key]))
            else:
                fallback = src.get(key)
                values.append(fallback[r] if isinstance(fallback, list) and r < len(fallback)
                              else _first(fallback) if fallback is not None else "")
        out[key] = values
    return settings


def _variant_rows(src: dict, n_src: int) -> list[int] | None:
    """bambu 2.x per-nozzle-type lists hold filaments x variants; the standard
    nozzle's entry for each filament"""
    variants = src.get("filament_extruder_variant") or []
    owners = src.get("filament_self_index") or []
    if not (isinstance(variants, list) and len(variants) > n_src and len(owners) == len(variants)):
        return None
    pick = []
    for slot in range(1, n_src + 1):
        rows = [i for i, owner in enumerate(owners) if str(owner) == str(slot)]
        if not rows:
            return None
        standard = [i for i in rows if variants[i] == STANDARD_VARIANT]
        pick.append((standard or rows)[0])
    return pick


def _tidy_leftovers(out: dict, src: dict, covered: set, n_src: int, reps: list[int],
                    filament_keys: set) -> None:
    # settings the target presets don't define: per-filament lists follow the
    # toolheads, anything else from the source printer goes and the slicer uses
    # its default. bambu-only options (e.g. support_object_skip_flush) break
    # slicing on other printers otherwise
    pick = _variant_rows(src, n_src)
    variants = len(src.get("filament_extruder_variant") or [])
    for key, value in src.items():
        if key in covered or key in PER_PLATE:
            continue
        if not isinstance(value, list):
            if key not in PROJECT_KEYS:
                out.pop(key, None)
            continue
        per_filament = key.startswith("filament_") or key in filament_keys
        n = len(value)
        if n in (0, 1):
            continue
        if per_filament and n == n_src:
            out[key] = [value[r] for r in reps]
        elif key == "flush_volumes_matrix" and n == n_src * n_src:
            out[key] = [value[a * n_src + b] for a in reps for b in reps]
        elif per_filament and pick is not None and n == variants:
            out[key] = [value[pick[r]] for r in reps]
        else:
            out.pop(key, None)
    if pick is not None or "filament_self_index" in out:
        out["filament_self_index"] = [str(i) for i in range(1, len(reps) + 1)]


def _identity(out: dict, lib: ProfileLibrary, machine: dict, process: dict,
              chosen: list[dict], changed: list[str], colours: list[str]) -> None:
    count = len(chosen)
    out["filament_colour"] = colours
    out["filament_settings_id"] = [p["name"] for p in chosen]
    out["filament_ids"] = [p.get("filament_id", "") for p in chosen]
    out["filament_map"] = ["1"] * count
    out["printer_settings_id"] = machine["name"]
    out["print_settings_id"] = process["name"]
    out["print_compatible_printers"] = process.get("compatible_printers", [machine["name"]])
    out["inherits_group"] = [""] + [p.get("_parent", "") for p in chosen] + [""]
    out["different_settings_to_system"] = [";".join(sorted(changed))] + [""] * (count + 1)
    out.pop("inherits", None)
    # needs one entry per toolhead, slicing fails otherwise
    if isinstance(machine.get("default_nozzle_volume_type"), list):
        out["nozzle_volume_type"] = list(machine["default_nozzle_volume_type"])
    try:
        default_bed = lib.resolve("machine", machine.get("printer_model", "")).get("default_bed_type", "")
    except KeyError:
        default_bed = ""  # packed (.opc) profiles don't carry the printer model file
    out["curr_bed_type"] = _bed_type(default_bed or machine.get("default_bed_type") or "",
                                     str(out.get("curr_bed_type") or ""), chosen)


# plate name -> the filament setting that holds its bed temperature (0 = unsupported)
BED_TEMPS = {
    "Textured PEI Plate": "textured_plate_temp",
    "High Temp Plate": "hot_plate_temp",
    "Cool Plate": "cool_plate_temp",
    "Engineering Plate": "eng_plate_temp",
    "Textured Cool Plate": "textured_cool_plate_temp",
    "Supertack Plate": "supertack_plate_temp",
}


def _bed_type(default: str, current: str, filaments: list[dict]) -> str:
    """the printer's default plate if every filament can use it, else the project's,
    else the first plate they all can. slicers refuse e.g. PETG on a Cool Plate"""
    def suits(plate: str) -> bool:
        key = BED_TEMPS.get(plate)
        if key is None:
            return False
        return all(_as_float(f.get(key), 1.0) > 0 for f in filaments)
    for plate in (default, current, *BED_TEMPS):
        if plate and suits(plate):
            return plate
    return default or current


def _rewrite_settings(src: dict, lib: ProfileLibrary, target: Target, overrides: dict[int, str],
                      plan: Plan, report: Report, colours: dict[int, str] | None = None) -> dict:
    if max(plan.merge) > target.toolheads or min(plan.merge) < 1:
        raise ConversionError(f"Toolheads must be between 1 and {target.toolheads}.")
    machine = lib.resolve("machine", target.machine)
    layer_height = _as_float(src.get("layer_height"), 0.2)
    process = choose_process(lib, target, layer_height)
    report.printer, report.process = machine["name"], process["name"]
    report.slot_word = target.slot_word

    chosen, reps = _slots(plan, target, lib, overrides, colours or {}, report)

    out = dict(src)
    out.update(settings_only(machine))
    process_settings = settings_only(process)
    out.update(process_settings)

    src_nozzle = _as_float(src.get("nozzle_diameter"), 0.4)
    dst_nozzle = _as_float(machine.get("nozzle_diameter"), 0.4)
    same_nozzle = abs(src_nozzle - dst_nozzle) < 1e-6
    keep = list(KEEP_PROCESS) + (list(KEEP_IF_SAME_NOZZLE) if same_nozzle else [])
    report.kept = changed = _carry_over(src, out, process_settings, keep, plan.merge)

    filament_settings = _filament_lists(out, src, chosen, reps)
    covered = (set(settings_only(machine)) | set(process_settings)
               | set().union(*filament_settings) | set(keep))
    filament_keys = set().union(*(settings_only(p) for p in plan.candidates)) if plan.candidates else set()
    _tidy_leftovers(out, src, covered, len(plan.filaments), reps, filament_keys)
    _identity(out, lib, machine, process, chosen, changed, [s.colour for s in report.slots])

    one_nozzle = len(_as_list(machine.get("nozzle_diameter"))) <= 1
    clash = _temperature_clash([settings_only(p) for p in chosen]) if one_nozzle else None
    if clash:
        report.warnings.append(
            f"{clash} need different nozzle temperatures, and this printer has one nozzle. "
            "Flash Studio and OrcaSlicer refuse to slice that unless 'Remove mixed temperature "
            "restriction' is turned on in their preferences; better to use materials that suit "
            "one temperature")

    min_lh = _as_float(machine.get("min_layer_height"), 0.0)
    max_lh = _as_float(machine.get("max_layer_height"), 99.0)
    if not min_lh <= layer_height <= max_lh:
        report.warnings.append(
            f"layer height {layer_height} mm is outside this printer's {min_lh}-{max_lh} mm range")
    if not same_nozzle:
        report.warnings.append(
            f"project was set up for a {src_nozzle} mm nozzle; line widths now use the "
            f"{dst_nozzle} mm defaults")
    return out


# --- the model files -------------------------------------------------------

def _plate_of_instances(model_settings: str | None) -> tuple[dict[tuple[str, str], int], int]:
    if not model_settings:
        return {}, 1
    root = ET.fromstring(model_settings)
    plates = root.findall("plate")
    where = {}
    for index, plate in enumerate(plates):
        meta = {m.get("key"): m.get("value") for m in plate.findall("metadata")}
        try:
            plate_index = int(meta.get("plater_id") or index + 1) - 1
        except ValueError:
            plate_index = index
        for inst in plate.findall("model_instance"):
            imeta = {m.get("key"): m.get("value") or "" for m in inst.findall("metadata")}
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
_ATTR = re.compile(r'([\w:]+)="([^"]*)"')


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


def _fit_prime_towers(settings: dict, box, plate_count: int, filaments: int) -> None:
    """keep every plate's prime tower on the bed. a position chosen for a bambu
    tower can push a bigger multi-toolhead tower off the edge, and with no
    position at all orca's default (15, 220) is too far back on a 256 mm bed"""
    if filaments < 2 or str(settings.get("enable_prime_tower", "1")) in ("0", "false"):
        return
    width = _as_float(settings.get("prime_tower_width"), 35.0)
    brim = max(_as_float(settings.get("prime_tower_brim_width"), 3.0), 3.0)
    volume = _as_float(settings.get("prime_volume"), 45.0)
    layer = max(_as_float(settings.get("layer_height"), 0.2), 0.05)
    # generous estimate of the tower's depth: every toolhead purging each layer
    depth = max(width, volume * filaments / (layer * width))
    margin = 5.0
    x0, y0, x1, y1 = box
    xs = _as_list(settings.get("wipe_tower_x")) or ["15"]
    ys = _as_list(settings.get("wipe_tower_y")) or ["220"]
    count = max(len(xs), len(ys), plate_count)

    def clamp(v, lo, hi):
        return f"{min(max(v, lo), max(lo, hi)):.3f}".rstrip("0").rstrip(".")
    settings["wipe_tower_x"] = [clamp(_as_float(xs[min(i, len(xs) - 1)], 15.0), x0 + brim + margin,
                                      x1 - width - brim - margin) for i in range(count)]
    settings["wipe_tower_y"] = [clamp(_as_float(ys[min(i, len(ys) - 1)], 220.0), y0 + brim + margin,
                                      y1 - depth - brim - margin) for i in range(count)]


def _drop_gcode_rels(rels_xml: str) -> str | None:
    """the relationships file without links to sliced g-code; None if nothing else is left"""
    kept = [r for r in re.findall(r"<Relationship\b[^>]*/>", rels_xml)
            if not re.search(r'Target="[^"]*\.gcode"', r, re.IGNORECASE)]
    root = re.search(r"<Relationships\b[^>]*>", rels_xml)
    if not kept or not root:
        return None
    return rels_xml[:root.end()] + "\n " + "\n ".join(kept) + "\n</Relationships>\n"


def _remap_numbers(pattern: str, text: str, mapping: dict[int, int]) -> str:
    return re.sub(pattern, lambda m: m.group(1) + str(mapping.get(int(m.group(2)), int(m.group(2))))
                  + m.group(3), text)


class _Conversion:
    """one source file being turned into one output file"""

    def __init__(self, zin: zipfile.ZipFile, source: Path, output: Path, target: Target,
                 lib: ProfileLibrary):
        self.zin, self.source, self.output, self.target, self.lib = zin, source, output, target, lib
        self.entries = {i.filename: i for i in zin.infolist()}
        self.replacements: dict[str, bytes | None] = {}
        self.models = {name: zin.read(name).decode("utf-8") for name in _model_entries(zin)}
        self.touched: set[str] = set()
        self.model_settings = (zin.read(MODEL_SETTINGS).decode("utf-8")
                               if MODEL_SETTINGS in self.entries else None)
        self.model_settings_changed = False
        self.report = Report(source, output, "", "")

    def text(self, name: str) -> str:
        return self.zin.read(name).decode("utf-8")

    def recolour(self, plan: Plan) -> None:
        """repaint triangles and renumber filaments for the toolhead layout"""
        mapping = {f.index: h for f, h in zip(plan.filaments, plan.merge, strict=True)}
        if all(k == v for k, v in mapping.items()):
            return
        for name, xml in self.models.items():
            new_xml, n = paint.remap_model(xml, mapping)
            if n:
                self.models[name] = new_xml
                self.touched.add(name)
                self.report.repainted += n
        if self.model_settings is not None:
            ms = _remap_numbers(_EXTRUDER_META.pattern, self.model_settings, mapping)
            self.model_settings = re.sub(
                r'(<metadata key="filament_maps" value=")[^"]*(")',
                lambda m: m.group(1) + " ".join(str(i) for i in range(1, max(plan.merge) + 1)) + m.group(2),
                ms)
            self.model_settings_changed = True
        if CUSTOM_GCODE in self.entries:
            xml = _remap_numbers(r'(\bextruder=")(\d+)(")', self.text(CUSTOM_GCODE), mapping)
            self.replacements[CUSTOM_GCODE] = xml.encode("utf-8")
        if len(set(plan.merge)) < len(plan.filaments) and FILAMENT_SEQUENCE in self.entries:
            try:
                seq = json.loads(self.zin.read(FILAMENT_SEQUENCE))
                for plate in seq.values():
                    if isinstance(plate, dict):
                        for k in plate:
                            plate[k] = []
                self.replacements[FILAMENT_SEQUENCE] = json.dumps(seq).encode("utf-8")
            except (ValueError, AttributeError):
                self.replacements[FILAMENT_SEQUENCE] = None

    def relocate(self, settings: dict, old_box, new_box, plate_of, plate_count) -> None:
        if old_box == new_box:
            return
        _move_prime_towers(settings, old_box, new_box)
        if MAIN_MODEL in self.models:
            self.models[MAIN_MODEL], self.report.moved_objects = _move_build_items(
                self.models[MAIN_MODEL], plate_of, plate_count, old_box, new_box)
            self.touched.add(MAIN_MODEL)
        if (new_box[2] - new_box[0] < old_box[2] - old_box[0]
                or new_box[3] - new_box[1] < old_box[3] - old_box[1]):
            self.report.warnings.append("the new bed is smaller than the original; check that every object fits")

    def relabel(self, src: dict, settings: dict) -> None:
        # flash studio refuses files from newer bambu versions
        target = self.target
        if _version(src.get("version")) <= _version(target.file_version):
            return
        settings["version"] = target.file_version
        if MAIN_MODEL in self.models:
            self.models[MAIN_MODEL] = _label_model(self.models[MAIN_MODEL], target)
            self.touched.add(MAIN_MODEL)
        if SLICE_INFO in self.entries:
            info = re.sub(r'(key="X-BBL-Client-Version" value=")[^"]*(")',
                          rf"\g<1>{target.file_version}\2", self.text(SLICE_INFO))
            if "OrcaSlicer-Version" not in info:
                info = re.sub(r'(<header_item key="X-BBL-Client-Version"[^>]*/>)',
                              rf'\1\n    <header_item key="OrcaSlicer-Version" value="{target.orca_version}"/>',
                              info)
            self.replacements[SLICE_INFO] = info.encode("utf-8")

    def check_fit(self, settings: dict, new_box, plate_of, plate_count) -> None:
        if MAIN_MODEL not in self.models:
            return
        columns = _plate_columns(plate_count)
        try:
            self.report.warnings += fit.check(
                self.models, MAIN_MODEL, plate_of, lambda i: _plate_centre(i, columns, new_box),
                new_box, _as_float(settings.get("printable_height"), 0.0),
                _object_names(self.model_settings))
        except (ValueError, KeyError, IndexError, ET.ParseError):
            self.report.warnings.append("couldn't check that objects fit the bed")

    def drop_gcode(self) -> None:
        for name in self.entries:
            if GCODE_ENTRY.match(name):
                self.replacements[name] = None
                self.report.removed_gcode = True
        if not self.report.removed_gcode or self.model_settings is None:
            return
        self.model_settings = re.sub(r'(<metadata key="gcode_file" value=")[^"]*(")', r"\1\2",
                                     self.model_settings)
        self.model_settings_changed = True
        if MODEL_SETTINGS_RELS in self.entries:
            rels = _drop_gcode_rels(self.text(MODEL_SETTINGS_RELS))
            self.replacements[MODEL_SETTINGS_RELS] = rels.encode("utf-8") if rels else None

    def write(self, settings: dict) -> None:
        self.replacements[PROJECT_SETTINGS] = json.dumps(settings, indent=4, ensure_ascii=False).encode("utf-8")
        for name in self.touched:
            self.replacements[name] = self.models[name].encode("utf-8")
        if self.model_settings_changed and self.model_settings is not None:
            self.replacements[MODEL_SETTINGS] = self.model_settings.encode("utf-8")

        self.output.parent.mkdir(parents=True, exist_ok=True)
        partial = self.output.with_name(self.output.name + ".partial")
        written: set[str] = set()
        try:
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zout:
                for entry in self.zin.infolist():
                    if entry.filename in written:
                        continue  # a duplicate entry in the source zip
                    written.add(entry.filename)
                    if entry.filename in self.replacements:
                        data = self.replacements[entry.filename]
                        if data is not None:
                            zout.writestr(_fresh_info(entry), data)
                        continue
                    with self.zin.open(entry) as src_fh, \
                            zout.open(_fresh_info(entry), "w", force_zip64=True) as dst_fh:
                        shutil.copyfileobj(src_fh, dst_fh, 1024 * 1024)
            partial.replace(self.output)
        finally:
            if partial.exists():
                partial.unlink()


def _fresh_info(info: zipfile.ZipInfo) -> zipfile.ZipInfo:
    fresh = zipfile.ZipInfo(info.filename, date_time=info.date_time)
    fresh.compress_type = zipfile.ZIP_DEFLATED
    fresh.external_attr = info.external_attr
    return fresh


def _check_colours(colours: dict[int, str] | None) -> dict[int, str]:
    bad = [c for c in (colours or {}).values() if not re.fullmatch(r"#?[0-9A-Fa-f]{6}", str(c).strip())]
    if bad:
        raise ConversionError(f"{bad[0]} isn't a colour; use #RRGGBB, e.g. #2E7D32.")
    return {t: c if str(c).startswith("#") else "#" + str(c) for t, c in (colours or {}).items()}


def convert(source: Path, output: Path, target: Target, lib: ProfileLibrary,
            overrides: dict[int, str] | None = None, merge: list[int] | None = None,
            plan: Plan | None = None, colours: dict[int, str] | None = None) -> Report:
    """write `output`, the project in `source` set up for `target`.
    merge: toolhead per source colour (default: the plan's). overrides: toolhead
    -> preset name. colours: toolhead -> #RRGGBB to show instead of the designer's.
    Every problem with the file comes back as a ConversionError."""
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve():
        raise ConversionError("The output would overwrite the original file.")
    colours = _check_colours(colours)
    with _plain_errors(source.name), _open(source) as zin:
        src = _read_project(zin, source.name)
        if src.get("printer_settings_id") == target.machine:
            raise ConversionError(
                f"{source.name} is already set up for the {target.label}. "
                "Open it in your slicer directly; there is nothing to convert.")
        plan = with_merge(plan or analyse(source, target, lib), merge)
        if max(plan.merge) > target.toolheads:
            raise ConversionError(
                f"This project uses {len(plan.filaments)} filaments and the {target.label} has "
                f"{target.toolheads}. Choose which toolhead each colour goes to.")

        job = _Conversion(zin, source, output, target, lib)
        settings = _rewrite_settings(src, lib, target, overrides or {}, plan, job.report, colours)
        if target.toolheads == 1 and len(plan.filaments) > 1:
            job.report.warnings.append("all colours print in one filament on this printer")
        job.recolour(plan)
        old_box = _bed_box(src.get("printable_area"))
        new_box = _bed_box(settings.get("printable_area"))
        plate_of, plate_count = _plate_of_instances(job.model_settings)
        job.relocate(settings, old_box, new_box, plate_of, plate_count)
        _fit_prime_towers(settings, new_box, plate_count, max(plan.merge))
        job.relabel(src, settings)
        job.check_fit(settings, new_box, plate_of, plate_count)
        job.drop_gcode()
        job.write(settings)
        return job.report
