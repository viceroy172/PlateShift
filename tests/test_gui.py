from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.destroy()
    HAVE_TK = True
except Exception:  # no tk, or no display (e.g. headless linux ci)
    HAVE_TK = False

from make_project import bambu_settings, write_project  # noqa: E402
from test_convert import MACHINE, make_profiles  # noqa: E402

from plateshift import paint  # noqa: E402
from plateshift.printers import Target  # noqa: E402
from plateshift.profiles import ProfileLibrary  # noqa: E402

FOUR = Target("four", "Four heads", "Testvendor", MACHINE, "0.20mm Standard @T", 4, "Four")
ONE = Target("one", "One head", "Testvendor", MACHINE, "0.20mm Standard @T", 1, "One")


@unittest.skipUnless(HAVE_TK, "needs Tk and a display")
class WindowTests(unittest.TestCase):
    def setUp(self):
        import plateshift.gui as gui
        self.gui = gui
        self.tmp = Path(tempfile.mkdtemp())
        lib = ProfileLibrary(make_profiles(self.tmp / "profiles"), "Testvendor")
        self.saved = (gui.TARGETS, gui._settings_file, gui.find_slicers)
        gui.TARGETS = [FOUR, ONE]
        gui._settings_file = lambda: self.tmp / "settings.json"
        gui.find_slicers = lambda: []
        (self.tmp / "settings.json").write_text(json.dumps({"printer": ONE.label, "test_slice": False}))
        self.root = tk.Tk()
        self.root.bell = lambda: None
        self.app = gui.App(self.root, [])
        self.app.library = lambda target: (target, lib)

    def tearDown(self):
        self.root.destroy()
        self.gui.TARGETS, self.gui._settings_file, self.gui.find_slicers = self.saved

    def wait(self, condition, timeout=20.0):
        end = time.time() + timeout
        while not condition():
            if time.time() > end:
                self.fail("window didn't get there in time")
            self.root.update()
            time.sleep(0.02)

    def six_colours(self) -> Path:
        colours = ["#000000", "#101010", "#FFFFFF", "#F0F0F0", "#FF0000", "#00AA00"]
        settings = bambu_settings(filament_type=["PLA"] * 6, filament_colour=colours,
                                  filament_settings_id=["Bambu PLA Basic @BBL X1C"] * 6)
        return write_project(self.tmp / "six.3mf", settings, [[(30 + 30 * i, 128) for i in range(6)]],
                             paint={i: paint.encode(i) for i in range(1, 7)})

    def panel_ready(self):
        return getattr(self.app, "plan", None) is not None and not self.app.busy

    def test_changing_printer_rebuilds_the_panel_for_it(self):
        # found by hand: panel kept the old printer's single toolhead and presets
        app = self.app
        self.assertEqual(app.printer.get(), ONE.label)
        app.handle_files([self.six_colours()])
        self.wait(self.panel_ready)
        self.assertEqual(app.plan_target.toolheads, 1)
        self.assertEqual(set(app.current_merge()), {1})

        app.printer.set(FOUR.label)
        self.wait(lambda: self.panel_ready() and app.plan_target.toolheads == 4)
        self.assertEqual(sorted(set(app.current_merge())), [1, 2, 3, 4])
        combos = [w for w in app.plan_frame.winfo_children()[0].winfo_children()[0].winfo_children()
                  if w.winfo_class() == "TCombobox"]
        self.assertTrue(combos and all(str(c.cget("state")) == "readonly" for c in combos))

        app.convert_plan()
        self.wait(lambda: not app.busy and app.last_output is not None)
        self.assertEqual(app.last_output.name, "six - Four.3mf")
        with zipfile.ZipFile(app.last_output) as z:
            s = json.loads(z.read("Metadata/project_settings.config"))
        self.assertEqual(len(s["filament_settings_id"]), 4)


if __name__ == "__main__":
    unittest.main()
