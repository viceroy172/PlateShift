# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fitting in with the system: PlateShift in the "Open with" list for .3mf files,
and noticing new .3mf downloads while the window is open."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

PROG_ID = "PlateShift.3mf"
DESKTOP_FILE = "plateshift.desktop"


def launch_command() -> list[str]:
    """how the system should start PlateShift with a file"""
    if getattr(sys, "frozen", False):
        return [os.environ.get("APPIMAGE") or sys.executable]
    # run from source: the system starts it in the file's folder, so say where plateshift is
    project = Path(__file__).resolve().parents[1]
    launcher = project / "plateshift.sh"
    if sys.platform != "win32" and launcher.is_file():
        return [str(launcher)]
    python = Path(sys.executable)
    if sys.platform == "win32" and python.name.lower() == "python.exe":
        windowed = python.with_name("pythonw.exe")
        python = windowed if windowed.exists() else python
    code = f"import sys; sys.path.insert(0, {str(project)!r}); from plateshift.__main__ import main; sys.exit(main())"
    return [str(python), "-c", code]


# --- open with ---------------------------------------------------------------

def open_with_supported() -> bool:
    return sys.platform in ("win32", "linux") or sys.platform.startswith("linux")


def _quote(parts: list[str]) -> str:
    return " ".join(f'"{p}"' for p in parts)


def add_open_with() -> None:
    """put PlateShift in the "Open with" list for .3mf files, without making it the default"""
    command = launch_command()
    if sys.platform == "win32":
        import winreg
        base = r"Software\Classes"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{base}\{PROG_ID}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "3MF project (PlateShift)")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{base}\{PROG_ID}\DefaultIcon") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"{command[0]},0")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{base}\{PROG_ID}\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, _quote(command) + ' "%1"')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{base}\.3mf\OpenWithProgids") as key:
            winreg.SetValueEx(key, PROG_ID, 0, winreg.REG_NONE, b"")
        _tell_explorer()
        return
    apps = _applications_dir()
    apps.mkdir(parents=True, exist_ok=True)
    exec_line = " ".join(_desktop_quote(p) for p in command) + " %F"
    (apps / DESKTOP_FILE).write_text(
        "[Desktop Entry]\nType=Application\nName=PlateShift\n"
        "Comment=Convert Bambu Studio / MakerWorld 3MF projects for other printers\n"
        f"Exec={exec_line}\nIcon=plateshift\nTerminal=false\nCategories=Graphics;3DGraphics;\n"
        "MimeType=model/3mf;application/vnd.ms-3mfdocument;\n", encoding="utf-8")
    _install_icon()
    try:
        subprocess.run(["update-desktop-database", str(apps)], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        pass  # the entry still works; menus pick it up later


def remove_open_with() -> None:
    if sys.platform == "win32":
        import winreg
        base = r"Software\Classes"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{base}\.3mf\OpenWithProgids", 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, PROG_ID)
        except OSError:
            pass
        for sub in (r"\shell\open\command", r"\shell\open", r"\shell", r"\DefaultIcon", ""):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"{base}\{PROG_ID}{sub}")
            except OSError:
                pass
        _tell_explorer()
        return
    (_applications_dir() / DESKTOP_FILE).unlink(missing_ok=True)


def has_open_with() -> bool:
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROG_ID}\shell\open\command"):
                return True
        except OSError:
            return False
    return (_applications_dir() / DESKTOP_FILE).is_file()


def _tell_explorer() -> None:
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
    except (AttributeError, OSError):
        pass


def _applications_dir() -> Path:
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data) / "applications"


def _desktop_quote(text: str) -> str:
    return f'"{text}"' if any(c in text for c in ' "\'\\') else text


def _install_icon() -> None:
    try:
        import base64
        from .icon_data import ICONS
        size = max(ICONS)
        folder = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / \
            "icons" / "hicolor" / f"{size}x{size}" / "apps"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "plateshift.png").write_bytes(base64.b64decode(ICONS[size]))
    except (OSError, ValueError):
        pass


# --- the downloads folder ----------------------------------------------------

def downloads_folder() -> Path:
    if sys.platform == "win32":
        try:
            import ctypes
            import uuid
            folder_id = uuid.UUID("374DE290-123F-4565-9164-39C4925E467B")  # FOLDERID_Downloads
            guid = (ctypes.c_byte * 16).from_buffer_copy(folder_id.bytes_le)
            path = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path)) == 0:
                found = Path(path.value)
                ctypes.windll.ole32.CoTaskMemFree(path)
                return found
        except (AttributeError, OSError, ValueError):
            pass
    elif sys.platform != "darwin":
        try:
            found = subprocess.run(["xdg-user-dir", "DOWNLOAD"], capture_output=True, text=True,
                                   timeout=5).stdout.strip()
            if found and Path(found) != Path.home():
                return Path(found)
        except (OSError, subprocess.SubprocessError):
            pass
    return Path.home() / "Downloads"


def is_converted(path: Path, suffixes) -> bool:
    """a file plateshift made, so it isn't offered again: "Robot - C5P.3mf",
    "Robot - C5P - plate 2.3mf", "Robot - C5P (2).3mf". suffixes: the printers' suffixes"""
    names = "|".join(re.escape(s) for s in sorted(set(suffixes), key=len, reverse=True) if s)
    if not names:
        return False
    return bool(re.search(rf" - (?:{names})( - plate \d+)?( \(\d+\))?\.3mf$", Path(path).name, re.IGNORECASE))


class DownloadWatcher:
    """new .3mf files in a folder, once each has finished downloading.
    check() is called every few seconds and returns the ones that are ready"""

    def __init__(self, folder: Path, suffixes=(), clock=time.time):
        self.folder = Path(folder)
        self.suffixes = tuple(suffixes)
        self.clock = clock
        self.since = clock()
        self.sizes: dict[Path, int] = {}   # still growing, or just seen
        self.done: set[Path] = set()

    def check(self) -> list[Path]:
        ready = []
        try:
            entries = list(self.folder.iterdir())
        except OSError:
            return []
        for path in entries:
            if path in self.done or path.suffix.lower() != ".3mf" or is_converted(path, self.suffixes):
                continue
            try:
                info = path.stat()
            except OSError:
                continue
            if info.st_mtime < self.since - 2:   # there before the watch began
                self.done.add(path)
                continue
            if self.sizes.get(path) == info.st_size and info.st_size > 0:
                ready.append(path)               # the same size twice: finished
                self.done.add(path)
                self.sizes.pop(path, None)
            else:
                self.sizes[path] = info.st_size
        return ready
