"""Drive the PlateShift window through the README states and screenshot each.

    python tools/screenshots.py --profiles <folder> --out <folder> [--slicer <program>]

macOS only (uses the system screencapture tool). Used by the macos workflow.
"""

from __future__ import annotations

import argparse
import json
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    work = Path(tempfile.mkdtemp())
    src = write_project(work / "bambu x1c project.3mf", e2e.bambu_project(args.profiles),
                        [[(60 + 40 * i, 128) for i in range(4)]], size=20,
                        paint={i: paint.encode(i) for i in range(1, 5)})
    settings = work / "settings.json"
    settings.write_text(json.dumps({"printer": "Flashforge Creator 5 Pro", "test_slice": True,
                                    "open_in_by_printer": {"c5p": "OrcaSlicer"}}))
    gui._settings_file = lambda: settings
    gui.save_settings = lambda s: None
    gui.open_in = lambda slicer, file: None   # don't actually launch the slicer

    root = tk.Tk()
    root.bell = lambda: None
    app = gui.App(root, [])
    root.geometry("1040x720+80+80")
    root.lift()
    root.attributes("-topmost", True)

    def shoot(name: str):
        root.update()
        x, y = root.winfo_rootx(), root.winfo_rooty()
        w, h = root.winfo_width(), root.winfo_height()
        title = 28  # the macos title bar sits above the tk content
        subprocess.run(["screencapture", "-x", f"-R{x},{y - title},{w},{h + title}",
                        str(args.out / f"mac_{name}.png")], check=True)
        print("captured", name, flush=True)

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
        fn()
        root.after(700, drive)

    step(lambda: shoot("main_screen"))
    step(lambda: app.handle_files([src]))
    step(lambda: None, lambda: getattr(app, "plan", None) is not None and not app.busy)
    step(lambda: app.merge_vars[3].set("3"))
    step(lambda: shoot("file_selected"))
    step(lambda: app.convert_plan())
    step(lambda: None, lambda: not app.busy and app.last_output is not None)
    step(lambda: shoot("done_results"))
    root.after(1500, drive)
    root.mainloop()
    print(app.log.get("1.0", "end").strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
