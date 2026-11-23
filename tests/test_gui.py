from __future__ import annotations

import json
import shutil
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
    _root.update_idletasks()
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
CREALITY = Target("cfs", "Slots", "Testvendor", MACHINE, "0.20mm Standard @T", 4, "Slots",
                  slicers=("Creality Print", "OrcaSlicer"), slot="Slot")


@unittest.skipUnless(HAVE_TK, "needs Tk and a display")
class WindowTests(unittest.TestCase):
    def setUp(self):
        import plateshift.gui as gui
        self.gui = gui
        self.tmp = Path(tempfile.mkdtemp())
        user = self.tmp / "user" / "process"
        user.mkdir(parents=True)
        (user / "0.20mm Mine @T.json").write_text(
            '{"name": "0.20mm Mine @T", "inherits": "0.20mm Standard @T", "wall_loops": "5"}', encoding="utf-8")
        lib = ProfileLibrary(make_profiles(self.tmp / "profiles"), "Testvendor", [("OrcaSlicer", user.parent)])
        self.saved = (gui.TARGETS, gui._settings_file, gui.find_slicers)
        gui.TARGETS = [FOUR, ONE, CREALITY]
        gui._settings_file = lambda: self.tmp / "settings.json"
        gui.find_slicers = lambda: []
        (self.tmp / "settings.json").write_text(json.dumps({"printer": ONE.label, "test_slice": False}))
        self.root = tk.Tk()
        self.root.bell = lambda: None
        self.app = gui.App(self.root, [])
        self.app.library = lambda target: (target, lib)

    def tearDown(self):
        for pending in self.root.tk.call("after", "info"):
            self.root.after_cancel(pending)
        self.root.destroy()
        shutil.rmtree(self.tmp, ignore_errors=True)
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

    def test_your_print_profile_is_offered_remembered_and_used(self):
        app = self.app
        app.printer.set(FOUR.label)
        mine = "0.20mm Mine @T  (yours, OrcaSlicer)"
        self.wait(lambda: mine in app.process_picker.cget("values"))
        values = list(app.process_picker.cget("values"))
        self.assertEqual(values[:2], [self.gui.AUTO_PROCESS, mine])
        self.assertEqual(str(app.keeper.cget("state")), "disabled")  # nothing to keep over automatic

        app.process_choice.set(mine)
        self.assertEqual(app.chosen_process(), "0.20mm Mine @T")
        self.assertEqual(str(app.keeper.cget("state")), "normal")
        saved = json.loads((self.tmp / "settings.json").read_text())
        self.assertEqual(saved["process_by_printer"][FOUR.key], "0.20mm Mine @T")

        # another printer starts on automatic; coming back restores the choice
        app.printer.set(ONE.label)
        self.wait(lambda: mine in app.process_picker.cget("values"))
        self.assertEqual(app.process_choice.get(), self.gui.AUTO_PROCESS)
        app.printer.set(FOUR.label)
        self.wait(lambda: app.process_choice.get() == mine)

        app.handle_files([self.six_colours()])
        self.wait(self.panel_ready)
        app.convert_plan()
        self.wait(lambda: not app.busy and app.last_output is not None)
        with zipfile.ZipFile(app.last_output) as z:
            s = json.loads(z.read("Metadata/project_settings.config"))
        self.assertEqual((s["print_settings_id"], s["wall_loops"]), ("0.20mm Mine @T", "5"))

    def test_about_shows_the_version_and_github_link(self):
        from plateshift import REPO_URL, __version__
        opened = []
        self.gui.webbrowser.open, saved = opened.append, self.gui.webbrowser.open
        self.addCleanup(setattr, self.gui.webbrowser, "open", saved)
        self.app.about()
        self.app.about()  # a second click brings the same window forward
        windows = [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)]
        self.assertEqual(len(windows), 1)
        labels = {}

        def walk(w):
            for c in w.winfo_children():
                if c.winfo_class() in ("Label", "TLabel"):
                    labels[str(c.cget("text"))] = c
                walk(c)
        walk(windows[0])
        self.assertIn(f"Version {__version__}", labels)
        self.assertFalse(any("@" in text for text in labels))  # no email address in the app
        self.root.update()  # tk only delivers clicks to widgets on screen
        labels[REPO_URL].event_generate("<Button-1>")
        self.assertEqual(opened, [REPO_URL])

    def test_brand_then_model(self):
        gui = self.gui
        other = Target("other", "Otherbrand Speedy", "Otherbrand", MACHINE, "0.20mm Standard @T", 1, "Speedy")
        gui.TARGETS = [FOUR, ONE, other]
        app = self.app
        app.show_printer()
        self.assertEqual(list(app.brand_picker.cget("values")), ["Testvendor", "Otherbrand"])
        app.brand.set("Otherbrand")
        self.assertEqual(app.printer.get(), "Otherbrand Speedy")
        self.assertEqual(app.model.get(), "Speedy")  # the brand isn't repeated
        self.assertEqual(list(app.picker.cget("values")), ["Speedy"])
        app.brand.set("Testvendor")
        self.assertEqual(app.printer.get(), ONE.label)  # the model last used for that brand
        app.model.set(FOUR.label)
        self.assertEqual(app.printer.get(), FOUR.label)
        app.brand.set("Otherbrand")
        app.brand.set("Testvendor")
        self.assertEqual(app.printer.get(), FOUR.label)

    def test_plate_picture_shows_and_enlarges(self):
        from test_preview import PNG
        src = self.six_colours()
        with zipfile.ZipFile(src, "a") as z:
            z.writestr("Metadata/plate_1.png", PNG)
            z.writestr("Metadata/plate_2.png", PNG)
        self.app.handle_files([src])
        self.wait(self.panel_ready)
        self.assertEqual(len(self.app.plate_images), 3)  # two plates and the small copy
        self.app.show_plates(src, self.app.plate_images[:2])
        self.assertTrue(self.app.plates_window.winfo_exists())

    def test_filaments_are_remembered_per_toolhead(self):
        app = self.app
        app.printer.set(FOUR.label)
        app.handle_files([self.six_colours()])
        self.wait(lambda: self.panel_ready() and app.plan_target.toolheads == 4)
        silk = next(text for text, name in app.preset_names.items() if name == "Generic PLA Silk @T")
        app.head_vars[2].set(silk)  # the user picks silk for toolhead 2
        app.convert_plan()
        self.wait(lambda: not app.busy and app.last_output is not None)
        saved = json.loads((self.tmp / "settings.json").read_text())
        self.assertEqual(saved["filaments_by_printer"][FOUR.key], {"2": "Generic PLA Silk @T"})

        # next time toolhead 2 starts on silk, marked as the usual one
        app.handle_files([self.six_colours()])
        self.wait(self.panel_ready)
        self.assertEqual(app.preset_names[app.head_vars[2].get()], "Generic PLA Silk @T")
        self.assertIn(2, app.head_usual)
        self.assertNotIn(1, app.head_usual)

    def test_test_slicer_follows_the_printer(self):
        app = self.app
        self.assertEqual(str(app.checker.cget("state")), "disabled")  # no slicers found
        flash = self.gui.Slicer("Flash Studio", Path("flash"))
        app.slicers = {"Flash Studio": flash}
        app.printer.set(FOUR.label)
        self.assertIs(app.tester, flash)
        self.assertIn("Flash Studio", str(app.checker.cget("text")))
        # flash studio hasn't got this printer's profiles
        app.printer.set(CREALITY.label)
        self.assertIsNone(app.tester)
        self.assertEqual(str(app.checker.cget("text")), "Test slice (needs OrcaSlicer)")
        self.assertEqual(str(app.checker.cget("state")), "disabled")

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

    def log(self) -> str:
        return self.app.log.get("1.0", "end")

    def test_an_unexpected_error_never_leaves_the_window_busy(self):
        app = self.app
        app.printer.set(FOUR.label)
        real = self.gui.analyse
        self.gui.analyse = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            app.handle_files([self.six_colours()])
            self.wait(lambda: not app.busy)
        finally:
            self.gui.analyse = real
        self.assertIn("unexpected problem (RuntimeError: boom)", self.log())

        # in a batch, one file failing oddly doesn't stop the next
        good = write_project(self.tmp / "good.3mf", bambu_settings(), [[(128, 128)]])
        real_convert = self.gui.convert
        calls = []

        def flaky(source, *a, **k):
            calls.append(source.name)
            if source.name == "six.3mf":
                raise RuntimeError("boom")
            return real_convert(source, *a, **k)
        self.gui.convert = flaky
        try:
            app.handle_files([self.six_colours(), good])
            self.wait(lambda: not app.busy and len(calls) == 2 and "Done" in app.status.cget("text"))
        finally:
            self.gui.convert = real_convert
        self.assertTrue((self.tmp / "good - Four.3mf").exists())
        self.assertIn("1 converted, 1 not converted", app.status.cget("text"))

    def test_existing_output_is_only_replaced_when_asked(self):
        app = self.app
        app.printer.set(FOUR.label)
        src = write_project(self.tmp / "keep.3mf", bambu_settings(), [[(128, 128)]])
        existing = self.tmp / "keep - Four.3mf"
        existing.write_text("my edited project")
        answers = iter([None, False])
        real = self.gui.messagebox.askyesnocancel
        self.gui.messagebox.askyesnocancel = lambda *a, **k: next(answers)
        try:
            app.handle_files([src])
            self.wait(self.panel_ready)
            app.convert_plan()                       # cancel: nothing happens, panel stays
            self.assertIsNotNone(app.plan)
            app.convert_plan()                       # no: keep both
            self.wait(lambda: not app.busy and app.last_output is not None)
        finally:
            self.gui.messagebox.askyesnocancel = real
        self.assertEqual(existing.read_text(), "my edited project")
        self.assertEqual(app.last_output.name, "keep - Four (2).3mf")

    def test_preset_warnings_show_in_the_panel(self):
        # the test printer has no TPU preset, so PLA is used and the panel says so
        app = self.app
        app.printer.set(FOUR.label)
        src = write_project(self.tmp / "tpu.3mf", bambu_settings(filament_type=["TPU", "PLA", "PLA", "PLA"]),
                            [[(128, 128)]])
        app.handle_files([src])
        self.wait(self.panel_ready)
        texts = [w.cget("text") for w in app.heads_frame.winfo_children() if w.winfo_class() == "TLabel"]
        self.assertTrue(any("no TPU preset" in t for t in texts), texts)


if __name__ == "__main__":
    unittest.main()
