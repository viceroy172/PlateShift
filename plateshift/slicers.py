"""Find installed slicers (Windows registry) and open files in them."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Slicer:
    label: str
    exe: Path


# label, exe, uninstall DisplayName pattern
KNOWN = [
    ("Flash Studio", "flash studio.exe", r"^Flash ?Studio"),
    ("OrcaSlicer", "orca-slicer.exe", r"^Orca ?Slicer"),
    ("Creality Print", "CrealityPrint.exe", r"^Creality ?Print"),
]

UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
UNINSTALL_32 = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
APP_PATHS = r"Software\Microsoft\Windows\CurrentVersion\App Paths"


def _registry_entries(path: str):
    import winreg
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue
        with key:
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    name = winreg.EnumKey(key, i)
                    with winreg.OpenKey(key, name) as sub:
                        values = {}
                        for j in range(winreg.QueryInfoKey(sub)[1]):
                            vname, value, _ = winreg.EnumValue(sub, j)
                            values[vname] = value
                except OSError:
                    continue
                yield name, values


def _store_alias(exe_name: str) -> Path | None:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        alias = Path(local) / "Microsoft" / "WindowsApps" / exe_name
        if os.path.lexists(alias):
            return alias
    return None


def _exe_from_uninstall(values: dict, exe_name: str) -> Path | None:
    icon = str(values.get("DisplayIcon") or "").split(",")[0].strip('"')
    if icon.lower().endswith(".exe") and Path(icon).is_file():
        return Path(icon)
    location = str(values.get("InstallLocation") or "").strip('"')
    if location:
        candidate = Path(location) / exe_name
        if candidate.is_file():
            return candidate
    return None


def find_slicers() -> list[Slicer]:
    if sys.platform != "win32":
        return []
    found: dict[str, Path] = {}
    entries = list(_registry_entries(UNINSTALL)) + list(_registry_entries(UNINSTALL_32))
    app_paths = {name.lower(): values for name, values in _registry_entries(APP_PATHS)}

    for label, exe_name, pattern in KNOWN:
        for _, values in entries:
            if re.search(pattern, str(values.get("DisplayName") or ""), re.IGNORECASE):
                exe = _exe_from_uninstall(values, exe_name)
                if exe:
                    found[label] = exe
                    break
        if label in found:
            continue
        registered = app_paths.get(exe_name.lower())
        if registered:
            target = Path(str(registered.get("") or "").strip('"'))
            # store apps cant be started from their install folder, use the alias
            if "WindowsApps" in target.parts:
                alias = _store_alias(exe_name)
                if alias:
                    found[label] = alias
            elif target.is_file():
                found[label] = target
    return [Slicer(label, found[label]) for label, _, _ in KNOWN if label in found]


def open_in(slicer: Slicer, file: Path) -> None:
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(slicer.exe), str(file)], cwd=str(Path(file).parent),
                     creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
