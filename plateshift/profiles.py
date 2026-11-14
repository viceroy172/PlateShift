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
        except (OSError, opc.OpcError):
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


def user_preset_dirs() -> list[tuple[str, Path]]:
    """(slicer name, user folder) for each slicer that keeps user presets as json"""
    found = []
    for base in _config_dirs():
        for label, folder in (("Flash Studio", "Orca-Flashforge"), ("OrcaSlicer", "OrcaSlicer")):
            users = base / folder / "user"
            if users.is_dir():
                found.extend((label, d) for d in sorted(users.iterdir()) if (d / "filament").is_dir())
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
        self._user: dict[str, tuple[str, Path]] = {}   # filament name -> (slicer, file)
        self._raw: dict[Path, dict] = {}
        self._resolved: dict[tuple[str, str], dict] = {}
        for folder in (vendor, SHARED_LIBRARY):
            base = self.root / folder
            found_json = False
            for kind in KINDS:
                kind_dir = base / kind
                if not kind_dir.is_dir():
                    continue
                for path in sorted(kind_dir.rglob("*.json")):
                    self._files[kind].setdefault(path.stem, []).append(path)
                    found_json = True
            # a vendor is either its json tree or its packed file, json wins
            if not found_json and (self.root / f"{folder}.opc").is_file():
                packed = _packed(self.root / f"{folder}.opc")
                for kind in KINDS:
                    for preset in (packed or {}).get(kind, []):
                        self._packed[kind].setdefault(preset["name"], preset)
        # old name -> current name, so presets made before a rename still find their parent
        self._renamed: dict[str, dict[str, str]] = {k: {} for k in KINDS}
        for kind in KINDS:
            for name, preset in self._packed[kind].items():
                for old in preset.get("renamed_from") or []:
                    self._renamed[kind].setdefault(old, name)
        for label, folder in user_dirs or []:
            for path in sorted((folder / "filament").glob("*.json")):
                try:
                    name = self._load(path).get("name") or path.stem
                except (OSError, ValueError):
                    continue
                self._user.setdefault(name, (label, path))

    def _load(self, path: Path) -> dict:
        data = self._raw.get(path)
        if data is None:
            data = self._raw[path] = _load_json(path)
        return data

    def _system(self, kind: str, name: str) -> bool:
        return bool(self._files[kind].get(name)) or name in self._packed[kind]

    def _raw_profile(self, kind: str, name: str) -> dict:
        for path in self._files[kind].get(name, []):
            data = self._load(path)
            if data.get("name", path.stem) == name:
                return data
        if name in self._packed[kind]:
            return self._packed[kind][name]
        if kind == "filament" and name in self._user:
            return self._load(self._user[name][1])
        if name in self._renamed[kind]:
            return self._raw_profile(kind, self._renamed[kind][name])
        # file name doesn't match, scan everthing
        for paths in self._files[kind].values():
            for path in paths:
                data = self._load(path)
                renamed = data.get("renamed_from") or []
                if data.get("name") == name or name in ([renamed] if isinstance(renamed, str) else renamed):
                    return data
        raise KeyError(f"No {kind} profile named {name!r} under {self.root}")

    def _own(self, kind: str, raw: dict, depth: int = 0) -> dict:
        """a preset's own values with its includes layered underneath"""
        includes = raw.get("_includes") or raw.get("include") or []
        if not includes or depth > 10:
            return raw
        merged: dict = {}
        for inc in ([includes] if isinstance(includes, str) else includes):
            merged.update(self._own(kind, self._raw_profile(kind, inc), depth + 1))
        merged.update(raw)
        return merged

    def resolve(self, kind: str, name: str) -> dict:
        key = (kind, name)
        if key not in self._resolved:
            chain = []
            seen = set()
            current = name
            while current:
                if current in seen:
                    raise ValueError(f"Inheritance loop in {kind} profile {name!r}")
                seen.add(current)
                raw = self._raw_profile(kind, current)
                chain.append(raw)
                current = raw.get("inherits") or ""
            merged: dict = {}
            for raw in reversed(chain):
                merged.update(self._own(kind, raw))
            merged["name"] = name
            # these don't inherit, except user presets take them from their parent
            user = kind == "filament" and name in self._user and not self._system(kind, name)
            for meta in ("compatible_printers", "compatible_prints", "filament_id",
                         "compatible_printers_condition", "instantiation"):
                source = next((r for r in chain if r.get(meta)), None) if user else chain[0]
                if source is not None and meta in source:
                    merged[meta] = source[meta]
                else:
                    merged.pop(meta, None)
            if user:
                merged["_user"] = self._user[name][0]
                merged["_parent"] = chain[1]["name"] if len(chain) > 1 else ""  # current name, after renames
                merged["instantiation"] = "true"
            self._resolved[key] = merged
        return self._resolved[key]

    def names(self, kind: str) -> list[str]:
        return sorted(set(self._files[kind]) | set(self._packed[kind]))

    def compatible(self, kind: str, machine_name: str, include_user: bool = False) -> list[dict]:
        names = self.names(kind)
        if include_user and kind == "filament":
            names += [n for n in self._user if not self._system(kind, n)]
        found = []
        for name in names:
            try:
                profile = self.resolve(kind, name)
            except (KeyError, ValueError, OSError, json.JSONDecodeError):
                continue
            if str(profile.get("instantiation", "true")).lower() != "true":
                continue
            if machine_name in (profile.get("compatible_printers") or []):
                found.append(profile)
        return found


def settings_only(profile: dict) -> dict:
    return {k: v for k, v in profile.items() if k not in META_KEYS and not k.startswith("_")}
