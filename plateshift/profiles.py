# SPDX-License-Identifier: AGPL-3.0-or-later
"""Load Orca-style system profiles and the user's own filament presets.

A vendor comes either as json files (<root>/<Vendor>/{machine,process,filament}/*.json)
or, from OrcaSlicer 2.4 on, packed into <root>/<Vendor>.opc (see opc.py).
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from pathlib import Path

from . import opc
from .condition import ConditionError, evaluate

KINDS = ("machine", "process", "filament")

META_KEYS = frozenset({
    "name", "inherits", "from", "instantiation", "setting_id", "type", "version",
    "description", "is_custom_defined", "renamed_from", "model_id", "filament_id",
    "compatible_printers", "compatible_printers_condition", "compatible_prints",
    "compatible_prints_condition", "filament_settings_id", "print_settings_id",
    "printer_settings_id", "include",
})

SHARED_LIBRARY = "OrcaFilamentLibrary"

_packed_cache: dict[Path, dict] = {}


def _packed(path: Path) -> dict | None:
    path = Path(path)
    if path not in _packed_cache:
        try:
            _packed_cache[path] = opc.read(path)
        except Exception:  # an unreadable or odd file just isn't used
            _packed_cache[path] = None
    return _packed_cache[path]


def _config_dirs() -> list[Path]:
    """where slicers keep their settings on this system"""
    home = Path.home()
    if sys.platform == "win32":
        return [Path(os.environ["APPDATA"])] if os.environ.get("APPDATA") else []
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support"]
    dirs = [Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")]
    dirs += sorted((home / ".var" / "app").glob("*/config"))  # flatpak
    return dirs


def default_profile_roots() -> list[Path]:
    roots = []
    for base in _config_dirs():
        roots += [base / "Orca-Flashforge" / "system", base / "OrcaSlicer" / "system"]
        roots += sorted(base.glob("Creality*Print*/system"), reverse=True)
    if sys.platform == "darwin":
        # the profiles shipped inside the slicer apps, for printers not added there yet
        for base in (Path("/Applications"), Path.home() / "Applications"):
            for app in ("Flash*Studio*.app", "Orca*Slicer*.app", "Creality*Print*.app"):
                roots += sorted(base.glob(f"{app}/Contents/Resources/profiles"), reverse=True)
                roots += sorted(base.glob(f"*/{app}/Contents/Resources/profiles"), reverse=True)
    if sys.platform == "win32":
        for program_files in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if not program_files:
                continue
            pf = Path(program_files)
            roots.append(pf / "Flashforge" / "Flash Studio Desktop" / "resources" / "profiles")
            roots.append(pf / "OrcaSlicer" / "resources" / "profiles")
            roots.extend(sorted(pf.glob("Creality/Creality Print*/resources/profiles"), reverse=True))
    extra = os.environ.get("PLATESHIFT_PROFILES")
    if extra:
        roots = [Path(p) for p in extra.split(os.pathsep) if p] + roots
    return roots


USER_KINDS = ("filament", "process")  # never machine: those hold the printer's address and keys


def user_preset_dirs() -> list[tuple[str, Path]]:
    """(slicer name, user folder) for each slicer that keeps user presets as json"""
    found = []
    for base in _config_dirs():
        places = [("Flash Studio", base / "Orca-Flashforge" / "user"), ("OrcaSlicer", base / "OrcaSlicer" / "user")]
        # creality print keeps a folder per version: newest first
        places += [("Creality Print", d) for d in sorted((base / "Creality").glob("Creality*Print*/*/user"),
                                                           reverse=True)]
        for label, users in places:
            if users.is_dir():
                found.extend((label, d) for d in sorted(users.iterdir())
                             if any((d / kind).is_dir() for kind in USER_KINDS))
    return found


def _has_machine(root: Path, vendor: str, machine: str) -> bool:
    if (root / vendor / "machine" / f"{machine}.json").is_file():
        return True
    packed = _packed(root / f"{vendor}.opc") if (root / f"{vendor}.opc").is_file() else None
    return bool(packed) and any(p["name"] == machine for p in packed["machine"])


def locate(target, candidates: list[Path] | None = None):
    """(target with the preset names found, profile root). Tries each of the
    target's preset name sets against each root, in order."""
    roots = candidates or default_profile_roots()
    for machine, process in target.preset_names():
        for root in roots:
            if _has_machine(root, target.vendor, machine):
                return dataclasses.replace(target, machine=machine, default_process=process), root
    looked = "\n  ".join(str(p) for p in roots)
    raise FileNotFoundError(
        f"Could not find {target.label} printer profiles. Install Flash Studio, OrcaSlicer or "
        f"Creality Print, or pass --profiles <folder>. Looked in:\n  {looked}")


def find_profile_root(vendor: str, candidates: list[Path] | None = None,
                      machine: str | None = None) -> Path:
    roots = candidates or default_profile_roots()
    for root in roots:
        if machine and _has_machine(root, vendor, machine):
            return root
        if not machine and ((root / vendor / "machine").is_dir() or (root / f"{vendor}.opc").is_file()):
            return root
    looked = "\n  ".join(str(p) for p in roots)
    raise FileNotFoundError(
        f"Could not find {machine or vendor} printer profiles. Install Flash Studio, "
        f"OrcaSlicer or Creality Print, or pass --profiles <folder>. Looked in:\n  {looked}")


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


class ProfileLibrary:
    def __init__(self, root: Path, vendor: str, user_dirs: list[tuple[str, Path]] | None = None):
        self.root = Path(root)
        self.vendor = vendor
        self._files: dict[str, dict[str, list[Path]]] = {k: {} for k in KINDS}
        self._packed: dict[str, dict[str, dict]] = {k: {} for k in KINDS}
        # kind -> preset name -> (slicer, file), for the user's own filament and print presets
        self._user: dict[str, dict[str, tuple[str, Path]]] = {k: {} for k in USER_KINDS}
        self._raw: dict[Path, dict] = {}
        self._resolved: dict[tuple[str, str], dict] = {}
        # presets from the printer brand's own folder (not the shared library); a
        # brand preset that names no printers suits all of that brand's printers
        self._brand: dict[str, set[str]] = {k: set() for k in KINDS}
        # name -> folder -> preset, for packed presets: the vendor and the shared library
        # can both have a base preset by the same name (fdm_filament_common)
        self._packed_in: dict[str, dict[str, dict[str, dict]]] = {k: {} for k in KINDS}
        for folder in (vendor, SHARED_LIBRARY):
            base = self.root / folder
            packed_file = self.root / f"{folder}.opc"
            # orca's rule: <vendor>.json beside the folder means the json tree is the
            # vendor; otherwise a .opc is the whole vendor and the folder only holds
            # images and helper files (e.g. filaments_color_codes.json)
            if packed_file.is_file() and not (self.root / f"{folder}.json").is_file():
                packed = _packed(packed_file)
                for kind in KINDS:
                    for preset in (packed or {}).get(kind, []):
                        self._packed[kind].setdefault(preset["name"], preset)
                        self._packed_in[kind].setdefault(preset["name"], {})[folder] = preset
                        if folder == vendor:
                            self._brand[kind].add(preset["name"])
                continue
            for kind in KINDS:
                kind_dir = base / kind
                if kind_dir.is_dir():
                    for path in sorted(kind_dir.rglob("*.json")):
                        self._files[kind].setdefault(path.stem, []).append(path)
                        if folder == vendor:
                            self._brand[kind].add(path.stem)
        # old name -> current name, so presets made before a rename still find their parent
        self._renamed: dict[str, dict[str, str]] = {k: {} for k in KINDS}
        for kind in KINDS:
            for name, preset in self._packed[kind].items():
                for old in preset.get("renamed_from") or []:
                    self._renamed[kind].setdefault(old, name)
        for label, folder in user_dirs or []:
            for kind in USER_KINDS:
                for path in sorted((folder / kind).glob("*.json")):
                    try:
                        name = self._load(path).get("name") or path.stem
                    except (OSError, ValueError):
                        continue
                    self._user[kind].setdefault(name, (label, path))

    def _load(self, path: Path) -> dict:
        data = self._raw.get(path)
        if data is None:
            data = self._raw[path] = _load_json(path)
        return data

    def _system(self, kind: str, name: str) -> bool:
        return bool(self._files[kind].get(name)) or name in self._packed[kind]

    def _folder(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).parts[0]
        except ValueError:
            return ""

    def _raw_profile(self, kind: str, name: str) -> dict:
        return self._locate(kind, name)[0]

    def _locate(self, kind: str, name: str, prefer: str | None = None) -> tuple[dict, str | None]:
        """(the preset, the folder it's in). prefer: the folder of the preset that asked
        for it. the slicers look a parent up beside its child first: a shared-library
        filament's fdm_filament_common is the library's (1.75 mm), not the vendor's,
        which can be for other printers (flashforge's says 2.85 mm)"""
        paths = self._files[kind].get(name, [])
        if prefer:
            for path in paths:
                if self._folder(path) == prefer:
                    data = self._load(path)
                    if data.get("name", path.stem) == name:
                        return data, prefer
            if prefer in self._packed_in[kind].get(name, {}):
                return self._packed_in[kind][name][prefer], prefer
        for path in paths:
            data = self._load(path)
            if data.get("name", path.stem) == name:
                return data, self._folder(path)
        if name in self._packed[kind]:
            folders = self._packed_in[kind].get(name, {})
            return self._packed[kind][name], next(iter(folders), None)
        if name in self._user.get(kind, {}):
            return self._load(self._user[kind][name][1]), None
        if name in self._renamed[kind]:
            return self._locate(kind, self._renamed[kind][name], prefer)
        # file name doesn't match, scan everthing
        for paths in self._files[kind].values():
            for path in paths:
                data = self._load(path)
                renamed = data.get("renamed_from") or []
                if data.get("name") == name or name in ([renamed] if isinstance(renamed, str) else renamed):
                    return data, self._folder(path)
        raise KeyError(f"No {kind} profile named {name!r} under {self.root}")

    def _own(self, kind: str, raw: dict, depth: int = 0, folder: str | None = None) -> dict:
        """a preset's own values with its includes layered underneath"""
        includes = raw.get("_includes") or raw.get("include") or []
        if not includes or depth > 10:
            return raw
        merged: dict = {}
        for inc in ([includes] if isinstance(includes, str) else includes):
            found, where = self._locate(kind, inc, folder)
            merged.update(self._own(kind, found, depth + 1, where))
        merged.update(raw)
        return merged

    def resolve(self, kind: str, name: str) -> dict:
        key = (kind, name)
        if key not in self._resolved:
            chain = []
            folders = []
            seen = set()
            current, folder = name, None
            while current:
                if current in seen:
                    raise ValueError(f"Inheritance loop in {kind} profile {name!r}")
                seen.add(current)
                # each parent is looked for beside its child first, as the slicers do
                raw, where = self._locate(kind, current, folder)
                folder = where or folder
                chain.append(raw)
                folders.append(folder)
                current = raw.get("inherits") or ""
            merged: dict = {}
            for raw, where in zip(reversed(chain), reversed(folders)):
                merged.update(self._own(kind, raw, folder=where))
            merged["name"] = name
            # these don't inherit, except user presets take them from their parent
            user = name in self._user.get(kind, {}) and not self._system(kind, name)
            for meta in ("compatible_printers", "compatible_prints", "filament_id",
                         "compatible_printers_condition", "instantiation"):
                source = next((r for r in chain if r.get(meta)), None) if user else chain[0]
                if source is not None and meta in source:
                    merged[meta] = source[meta]
                else:
                    merged.pop(meta, None)
            if user:
                merged["_user"] = self._user[kind][name][0]
                merged["_parent"] = chain[1]["name"] if len(chain) > 1 else ""  # current name, after renames
                merged["instantiation"] = "true"
            self._resolved[key] = merged
        return self._resolved[key]

    def names(self, kind: str) -> list[str]:
        return sorted(set(self._files[kind]) | set(self._packed[kind]))

    def compatible(self, kind: str, machine_name: str, include_user: bool = False) -> list[dict]:
        """presets of `kind` that suit the printer, by orca's rules:
        1. a preset that lists printers suits exactly those (marked _explicit);
        2. otherwise its compatible_printers_condition decides;
        3. otherwise a filament from the printer's own brand suits all that brand's printers.
        Each result is a copy, so marks for this printer don't leak into the cache."""
        names = self.names(kind)
        if include_user:
            names += [n for n in self._user.get(kind, {}) if not self._system(kind, n)]
        machine_settings = None
        found = []
        for name in names:
            try:
                profile = self.resolve(kind, name)
            except (KeyError, ValueError, OSError, json.JSONDecodeError):
                continue
            if str(profile.get("instantiation", "true")).lower() != "true":
                continue
            listed = profile.get("compatible_printers") or []
            condition = str(profile.get("compatible_printers_condition") or "").strip()
            if listed:
                if machine_name in listed:
                    found.append(dict(profile, _explicit=True))
                continue
            if condition:
                if machine_settings is None:
                    machine = self.resolve("machine", machine_name)
                    machine_settings = dict(settings_only(machine), printer_settings_id=machine_name)
                try:
                    suits = evaluate(condition, machine_settings)
                except (ConditionError, IndexError, ValueError):
                    suits = False  # unreadable: hide it rather than offer the wrong preset
                if suits:
                    found.append(dict(profile))
                continue
            root = name if not profile.get("_user") else profile.get("_parent", "")
            if kind == "filament" and root in self._brand[kind]:
                found.append(dict(profile))
        return found


def settings_only(profile: dict) -> dict:
    return {k: v for k, v in profile.items() if k not in META_KEYS and not k.startswith("_")}
