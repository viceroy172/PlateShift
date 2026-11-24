# SPDX-License-Identifier: AGPL-3.0-or-later
"""Writes plateshift/licence_texts.py: the licences of PlateShift and of what the
built app carries (Python, Tcl/Tk, PyInstaller's launcher), for the Licences window.

Run with the Python the exe is built with, from the project folder:
    .venv\\Scripts\\python.exe tools\\make_licences.py
"""

from __future__ import annotations

import sys
import tkinter
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").strip() + "\n"


def _python_licence() -> Path:
    base = Path(sys.base_prefix)
    for path in (base / "LICENSE.txt", base / "LICENSE",
                 Path(sys.prefix) / "LICENSE.txt",
                 base / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "LICENSE.txt"):
        if path.is_file():
            return path
    raise SystemExit("can't find Python's LICENSE.txt")


def _tk_licence() -> Path:
    tk_dir = Path(tkinter.Tcl().eval("info library")).parent
    for path in sorted(tk_dir.glob("tk*/license.terms")) + sorted(tk_dir.glob("tcl*/license.terms")):
        return path
    raise SystemExit(f"can't find Tcl/Tk's license.terms under {tk_dir}")


def _pyinstaller_licence() -> str:
    dist = metadata.distribution("pyinstaller")
    for f in dist.files or []:
        if f.name.upper().startswith("COPYING"):
            return _read(Path(dist.locate_file(f)))
    raise SystemExit("can't find PyInstaller's COPYING.txt (pip install pyinstaller)")


def main() -> int:
    entries = [
        ("PlateShift", "GNU Affero General Public License v3 or later", _read(ROOT / "LICENSE")),
        ("Python", "Python Software Foundation License, and the licences of the libraries it includes",
         _read(_python_licence())),
        ("Tcl/Tk", "Tcl/Tk licence (BSD style), for the window toolkit", _read(_tk_licence())),
        ("PyInstaller", "GNU GPL v2 with an exception for the launcher in the Windows and Mac apps",
         _pyinstaller_licence()),
    ]
    out = ['# SPDX-License-Identifier: AGPL-3.0-or-later',
           '"""Licence texts for the Licences window, made by tools/make_licences.py. Don\'t edit by hand."""',
           "", "LICENCES = ["]
    for name, summary, text in entries:
        out.append(f"    ({name!r}, {summary!r},\n     {text!r}),")
    out.append("]\n")
    target = ROOT / "plateshift" / "licence_texts.py"
    target.write_text("\n".join(out), encoding="utf-8", newline="\n")
    print(f"wrote {target} ({target.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
