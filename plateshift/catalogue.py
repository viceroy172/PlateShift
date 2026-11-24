# SPDX-License-Identifier: AGPL-3.0-or-later
"""Printers the installed slicers have profiles for, so you can add one that
PlateShift doesn't list itself."""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from pathlib import Path

from .printers import Target
from .profiles import ProfileLibrary, default_profile_roots

# vendor folders that aren't printers you'd convert for
_SKIP = {"Custom", "custom", "OrcaFilamentLibrary", "Generic", "BBL_Template"}


def slicer_of(root: Path) -> str:
    """which slicer a profile folder belongs to, by where it is"""
    text = str(root).lower()
    if "flash studio" in text or "orca-flashforge" in text:
        return "Flash Studio"
    if "creality" in text:
        return "Creality Print"
    return "OrcaSlicer"


def vendors(roots: list[Path] | None = None) -> dict[str, Path]:
    """vendor -> the first profile folder that has its printers"""
    found: dict[str, Path] = {}
    for root in roots or default_profile_roots():
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if entry.is_dir() and (entry / "machine").is_dir():
                name = entry.name
            elif entry.suffix == ".opc" and entry.is_file():
                name = entry.stem
            else:
                continue
            if name not in _SKIP:
                found.setdefault(name, root)
    return dict(sorted(found.items(), key=lambda kv: kv[0].lower()))


@dataclass
class Machine:
    vendor: str
    name: str            # the machine preset, eg "Prusa MK4S 0.4 nozzle"
    model: str           # eg "Prusa MK4S"
    nozzle: str          # eg "0.4"
    nozzles: int         # how many nozzles (toolheads)
    colour_unit: bool    # one nozzle that can be fed by an AMS, CFS, MMU...
    default_process: str
    slicer: str

    @property
    def label(self) -> str:
        nozzle = "" if self.nozzle in ("", "0.4") else f" {self.nozzle} nozzle"
        return f"{self.model}{nozzle}"


def _first(value) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return "" if value is None else str(value)


def machines(vendor: str, root: Path) -> list[Machine]:
    """the printers (instantiable machine presets) a vendor's profiles offer"""
    lib = ProfileLibrary(root, vendor)
    out = []
    for name in lib.names("machine"):
        try:
            p = lib.resolve("machine", name)
        except (KeyError, ValueError, OSError):
            continue
        if str(p.get("instantiation", "")).lower() != "true":
            continue
        nozzles = p.get("nozzle_diameter") or ["0.4"]
        nozzles = nozzles if isinstance(nozzles, list) else [nozzles]
        out.append(Machine(
            vendor=vendor, name=name,
            model=_first(p.get("printer_model")) or re.sub(r"\s+[\d.]+\s*nozzle$", "", name),
            nozzle=_first(nozzles), nozzles=max(len(nozzles), 1),
            colour_unit=_first(p.get("single_extruder_multi_material")) == "1",
            default_process=_default_process(lib, p, name), slicer=slicer_of(root)))
    return sorted(out, key=lambda m: (m.model.lower(), float(m.nozzle or 0)))


def _default_process(lib: ProfileLibrary, machine: dict, name: str) -> str:
    wanted = _first(machine.get("default_print_profile"))
    if wanted and wanted in lib.names("process"):
        return wanted
    # else the compatible print profile closest to 0.2 mm layers
    best, gap = "", 99.0
    for p in lib.compatible("process", name):
        try:
            height = float(_first(p.get("layer_height")) or 0)
        except ValueError:
            continue
        if abs(height - 0.2) < gap:
            best, gap = p["name"], abs(height - 0.2)
    return best or wanted


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def make_target(machine: Machine, colours: int | None = None) -> Target:
    """a Target for a printer you add. colours: how many filaments it prints at once
    (default: one per nozzle); more than one on a single nozzle means a colour unit"""
    colours = max(1, colours or machine.nozzles)
    unit = machine.nozzles == 1 and colours > 1
    label = machine.label if machine.label.startswith(machine.vendor) else f"{machine.vendor} {machine.label}"
    if unit:
        label += f" ({colours} slots)"
    suffix = machine.label[len(machine.vendor):].strip() if machine.label.startswith(machine.vendor) else machine.label
    slicers = tuple(dict.fromkeys((machine.slicer, "OrcaSlicer")))
    return Target(
        key=f"my-{_slug(machine.name)}" + (f"-{colours}" if unit else ""), label=label, vendor=machine.vendor,
        machine=machine.name, default_process=machine.default_process, toolheads=colours,
        suffix=suffix or machine.name, slicers=slicers, slot="Slot" if unit else "",
        note=("One nozzle fed by a colour unit: colours sharing a slot print in that slot's filament."
              if unit else ""))


def to_dict(target: Target) -> dict:
    return dataclasses.asdict(target)


def from_dict(data: dict) -> Target | None:
    """a saved printer, or None if it can't be read"""
    fields = {f.name for f in dataclasses.fields(Target)}
    try:
        values = {k: v for k, v in data.items() if k in fields}
        values["slicers"] = tuple(values.get("slicers") or ("OrcaSlicer",))
        values["alternatives"] = tuple(tuple(a) for a in values.get("alternatives") or ())
        values["toolheads"] = int(values["toolheads"])
        return Target(**values)
    except (TypeError, KeyError, ValueError):
        return None
