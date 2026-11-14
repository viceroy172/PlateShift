"""Find installed slicers and open files in them.

Windows: the registry (desktop installs under Uninstall, store apps under App
Paths with a launcher alias). Linux: commands on PATH, flatpaks, AppImages in
the usual folders.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Slicer:
    label: str
    exe: Path
    args: tuple[str, ...] = ()   # e.g. ("run", "<app id>") for a flatpak

    def command(self, *more: str) -> list[str]:
        return [str(self.exe), *self.args, *more]


# label, exe, uninstall DisplayName pattern
KNOWN = [
    ("Flash Studio", "flash studio.exe", r"^Flash ?Studio"),
    ("OrcaSlicer", "orca-slicer.exe", r"^Orca ?Slicer"),
    ("Creality Print", "CrealityPrint.exe", r"^Creality ?Print"),
]

# label, commands, flatpak app id pattern, AppImage file pattern
LINUX = [
    ("OrcaSlicer", ("orca-slicer", "OrcaSlicer", "orcaslicer"), r"orcaslicer", r"orca.?slicer.*\.appimage$"),
    ("Creality Print", ("CrealityPrint", "creality-print"), r"crealityprint", r"creality.?print.*\.appimage$"),
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


def _windows() -> list[Slicer]:
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


def _flatpaks() -> list[str]:
    flatpak = shutil.which("flatpak")
    if not flatpak:
        return []
    try:
        out = subprocess.run([flatpak, "list", "--app", "--columns=application"],
                             capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _appimage_dirs() -> list[Path]:
    home = Path.home()
    return [home / "Applications", home / "AppImages", home / ".local" / "bin", home / "bin",
            home / "Downloads", home, Path("/opt"), Path("/opt/AppImages")]


def _linux() -> list[Slicer]:
    found = []
    apps = None
    for label, commands, flatpak_id, appimage in LINUX:
        exe = next((shutil.which(c) for c in commands if shutil.which(c)), None)
        if exe:
            found.append(Slicer(label, Path(exe)))
            continue
        if apps is None:
            apps = _flatpaks()
        app = next((a for a in apps if re.search(flatpak_id, a, re.IGNORECASE)), None)
        if app:
            found.append(Slicer(label, Path(shutil.which("flatpak")), ("run", app)))
            continue
        images = []
        for d in _appimage_dirs():
            try:
                images += [p for p in d.iterdir() if p.is_file() and re.search(appimage, p.name, re.IGNORECASE)]
            except OSError:
                continue
        if images:
            newest = max(images, key=lambda p: p.stat().st_mtime)
            found.append(Slicer(label, newest))
    return found


def find_slicers() -> list[Slicer]:
    if sys.platform == "win32":
        return _windows()
    if sys.platform.startswith("linux"):
        return _linux()
    return []


def test_slicer(slicers: dict[str, Slicer]) -> Slicer | None:
    """the slicer used for test slicing: flash studio where there is one, else orca"""
    return slicers.get("Flash Studio") or slicers.get("OrcaSlicer")


def open_in(slicer: Slicer, file: Path) -> None:
    flags = 0
    extra = {}
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        extra["start_new_session"] = True
    subprocess.Popen(slicer.command(str(Path(file).resolve())), cwd=str(Path(file).parent),
                     creationflags=flags, close_fds=True, **extra,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def show_in_folder(file: Path) -> bool:
    file = Path(file)
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(file)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(file)])
        else:
            subprocess.Popen(["xdg-open", str(file.parent)], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False  # no file manager helper (e.g. a minimal linux install)
    return True
