# SPDX-License-Identifier: AGPL-3.0-or-later
"""A zip of what's needed to look into a problem: the files, the log and versions.
Nothing is sent anywhere; you attach the zip to a GitHub issue yourself."""

from __future__ import annotations

import json
import platform
import sys
import time
import urllib.parse
import zipfile
from pathlib import Path

from . import REPO_URL, __version__

MAX_FILE = 200 * 1024 * 1024   # bigger projects are left out, with a note


def details(printer: str = "", slicers: dict[str, str] | None = None) -> str:
    import tkinter
    lines = [f"PlateShift {__version__}",
             f"System: {platform.system()} {platform.release()} ({platform.machine()})",
             f"Python {platform.python_version()}, Tk {tkinter.TkVersion}",
             f"Frozen app: {'yes' if getattr(sys, 'frozen', False) else 'no'}"]
    if printer:
        lines.append(f"Printer: {printer}")
    for name, where in (slicers or {}).items():
        lines.append(f"Slicer found: {name} ({where})")
    return "\n".join(lines)


def make(folder: Path, files: list[Path], log: str, about: str, settings: dict | None = None) -> Path:
    """write 'PlateShift problem report <date>.zip' in folder and return its path"""
    folder = Path(folder)
    stamp = time.strftime("%Y-%m-%d %H%M")
    path = folder / f"PlateShift problem report {stamp}.zip"
    n = 2
    while path.exists():
        path = folder / f"PlateShift problem report {stamp} ({n}).zip"
        n += 1
    skipped = []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("about.txt", about + "\n")
        z.writestr("log.txt", log)
        if settings is not None:
            z.writestr("settings.json", json.dumps(settings, indent=2, ensure_ascii=False))
        seen = set()
        for f in files:
            f = Path(f)
            if f.name in seen or not f.is_file():
                continue
            seen.add(f.name)
            if f.stat().st_size > MAX_FILE:
                skipped.append(f.name)
                continue
            z.write(f, f"files/{f.name}")
        if skipped:
            z.writestr("left out.txt", "Too big to include:\n" + "\n".join(skipped) + "\n")
    return path


def issue_url(title: str, about: str) -> str:
    """a new GitHub issue with the details filled in; the zip is dragged on by hand"""
    body = ("**What happened?**\n\n\n**What did you expect?**\n\n\n"
            "**Problem report:** drag the PlateShift problem report zip onto this box.\n\n"
            f"```\n{about}\n```\n")
    return f"{REPO_URL}/issues/new?" + urllib.parse.urlencode({"title": title, "body": body})
