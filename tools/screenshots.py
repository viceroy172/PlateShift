# SPDX-License-Identifier: AGPL-3.0-or-later
"""Drive the PlateShift window through the README states and screenshot each.

    python tools/screenshots.py --profiles <folder with BBL profiles> --out <folder>

Windows and Linux under WSL capture the window through Windows (WSL windows are
ordinary Windows windows); macOS uses its screencapture tool, as the macos
workflow does. Files are named main_screen.png etc., with linux_ or mac_ in front.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "tests")]

import e2e  # noqa: E402
from make_project import write_project  # noqa: E402

import plateshift.gui as gui  # noqa: E402
from plateshift import paint  # noqa: E402

TITLE = "PlateShift"
PREFIX = {"win32": "", "darwin": "mac_"}.get(sys.platform, "linux_")

# finds the visible top-level window with this title and saves what's on screen there,
# title bar included and without the drop shadow
_CAPTURE = r'''
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices;
public class Shot {
  public delegate bool EnumProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr p);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr h, int a, out RECT r, int n);
  public struct RECT { public int L, T, R, B; }
  public static RECT Find(string title) {
    SetProcessDPIAware();
    RECT found = new RECT();
    EnumWindows((h, p) => {
      var s = new StringBuilder(256); GetWindowText(h, s, 256);
      var t = s.ToString();
      // wsl windows carry the distro's name: "PlateShift (Ubuntu)"
      if (IsWindowVisible(h) && (t == title || t.StartsWith(title + " ("))) { DwmGetWindowAttribute(h, 9, out found, 16); return false; }
      return true; }, IntPtr.Zero);
    return found;
  }
}
"@
$r = [Shot]::Find('TITLE'); if ($r.R -le $r.L) { throw 'window not found' }
$b = New-Object Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T)
$g = [Drawing.Graphics]::FromImage($b); $g.CopyFromScreen($r.L, $r.T, 0, 0, $b.Size)
$b.Save('OUT')
'''


def _windows_path(path: Path) -> str:
    if sys.platform == "win32":
        return str(path)
    return subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)

    # four painted cubes on two plates, so the plate ticks show
    work = Path(tempfile.mkdtemp())
    step_x = 256 * 1.2
    src = write_project(work / "Colour cubes.3mf", e2e.bambu_project(args.profiles),
                        [[(70 + 45 * i, 128) for i in range(3)], [(128 + step_x, 128)]], size=20,
                        paint={i: paint.encode(i) for i in range(1, 5)})
    settings = work / "settings.json"
    settings.write_text(json.dumps({"printer": "Flashforge Creator 5 Pro", "test_slice": True, "currency": "$",
                                    "open_in_by_printer": {"c5p": "OrcaSlicer"}}))
    gui._settings_file = lambda: settings
    gui.save_settings = lambda s: None
    gui.open_in = lambda slicer, file: None   # don't actually launch the slicer

    root = tk.Tk()
    root.bell = lambda: None
    app = gui.App(root, [])
    root.title(TITLE)
    # the ci mac screen is only 1024 x 768
    root.geometry("960x700+20+32" if sys.platform == "darwin" else "1300x800+40+40")
    root.lift()
    root.attributes("-topmost", True)

    def shoot(name: str):
        root.update()
        out = args.out / f"{PREFIX}{name}.png"
        if sys.platform == "darwin":
            x, y = root.winfo_rootx(), root.winfo_rooty()
            w, h = root.winfo_width(), root.winfo_height()
            title = 28  # the macos title bar sits above the tk content
            subprocess.run(["screencapture", "-x", f"-R{x},{y - title},{w},{h + title}", str(out)], check=True)
        else:
            shell = "powershell" if sys.platform == "win32" else shutil.which("powershell.exe") or "powershell.exe"
            script = _CAPTURE.replace("TITLE", TITLE).replace("OUT", _windows_path(out.resolve()))
            subprocess.run([shell, "-NoProfile", "-Command", script], check=True)
        print("captured", out.name, flush=True)

    steps = []

    def step(fn, cond=None):
        steps.append((fn, cond))

    def drive():
        if not steps:
            root.after(300, root.quit)
            return
        fn, cond = steps[0]
        if cond and not cond():
            root.after(200, drive)
            return
        steps.pop(0)
        try:
            fn()
        except Exception:
            root.quit()  # don't sit waiting for steps that will never come
            raise
        root.after(700, drive)

    step(lambda: shoot("main_screen"))
    step(lambda: app.handle_files([src]))
    step(lambda: None, lambda: getattr(app, "plan", None) is not None and not app.busy)
    step(lambda: app.merge_vars[3].set("3"))
    step(lambda: shoot("file_selected"))
    step(lambda: app.convert_plan())
    step(lambda: None, lambda: not app.busy and app.last_output is not None)
    step(lambda: app.log.see("end"))
    step(lambda: shoot("done_results"))
    root.after(1500, drive)
    root.mainloop()
    sys.stdout.reconfigure(errors="replace")  # the log has ✓ in it
    print(app.log.get("1.0", "end").strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
