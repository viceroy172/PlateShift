"""Load Orca-style system profiles (<root>/<Vendor>/{machine,process,filament}/*.json)
and the user's own filament presets."""

from __future__ import annotations

import json
import os
from pathlib import Path

KINDS = ("machine", "process", "filament")

META_KEYS = frozenset({
    "name", "inherits", "from", "instantiation", "setting_id", "type", "version",
    "description", "is_custom_defined", "renamed_from", "model_id", "filament_id",
    "compatible_printers", "compatible_printers_condition", "compatible_prints",
    "compatible_prints_condition", "filament_settings_id", "print_settings_id",
    "printer_settings_id",
})

SHARED_LIBRARY = "OrcaFilamentLibrary"


def default_profile_roots() -> list[Path]:
    roots = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "Orca-Flashforge" / "system")
    for program_files in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if not program_files:
            continue
        pf = Path(program_files)
        roots.append(pf / "Flashforge" / "Flash Studio Desktop" / "resources" / "profiles")
        roots.append(pf / "OrcaSlicer" / "resources" / "profiles")
        roots.extend(sorted(pf.glob("Creality/Creality Print*/resources/profiles"), reverse=True))
    return roots


def user_preset_dirs() -> list[tuple[str, Path]]:
    """(slicer name, user folder) for each slicer that keeps user presets as json"""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return []
    found = []
    for label, folder in (("Flash Studio", "Orca-Flashforge"), ("OrcaSlicer", "OrcaSlicer")):
        base = Path(appdata) / folder / "user"
        if base.is_dir():
            found.extend((label, d) for d in sorted(base.iterdir()) if (d / "filament").is_dir())
    return found


def find_profile_root(vendor: str, candidates: list[Path] | None = None,
                      machine: str | None = None) -> Path:
    roots = candidates or default_profile_roots()
    for root in roots:
        if not (root / vendor / "machine").is_dir():
            continue
        if machine and not (root / vendor / "machine" / f"{machine}.json").is_file():
            continue
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
        self._user: dict[str, tuple[str, Path]] = {}   # filament name -> (slicer, file)
        self._raw: dict[Path, dict] = {}
        self._resolved: dict[tuple[str, str], dict] = {}
        for folder in (vendor, SHARED_LIBRARY):
            base = self.root / folder
            for kind in KINDS:
                kind_dir = base / kind
                if not kind_dir.is_dir():
                    continue
                for path in sorted(kind_dir.rglob("*.json")):
                    self._files[kind].setdefault(path.stem, []).append(path)
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

    def _raw_profile(self, kind: str, name: str) -> dict:
        for path in self._files[kind].get(name, []):
            data = self._load(path)
            if data.get("name", path.stem) == name:
                return data
        if kind == "filament" and name in self._user:
            return self._load(self._user[name][1])
        # file name doesn't match, scan everthing
        for paths in self._files[kind].values():
            for path in paths:
                if self._load(path).get("name") == name:
                    return self._load(path)
        raise KeyError(f"No {kind} profile named {name!r} under {self.root}")

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
                merged.update(raw)
            merged["name"] = name
            # these don't inherit, except user presets take them from their parent
            user = kind == "filament" and name in self._user and not self._files[kind].get(name)
            for meta in ("compatible_printers", "compatible_prints", "filament_id",
                         "compatible_printers_condition", "instantiation"):
                source = next((r for r in chain if r.get(meta)), None) if user else chain[0]
                if source is not None and meta in source:
                    merged[meta] = source[meta]
                else:
                    merged.pop(meta, None)
            if user:
                merged["_user"] = self._user[name][0]
                merged["_parent"] = chain[0].get("inherits", "")
                merged["instantiation"] = "true"
            self._resolved[key] = merged
        return self._resolved[key]

    def names(self, kind: str) -> list[str]:
        return sorted(self._files[kind])

    def compatible(self, kind: str, machine_name: str, include_user: bool = False) -> list[dict]:
        names = self.names(kind)
        if include_user and kind == "filament":
            names += [n for n in self._user if n not in self._files[kind]]
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
