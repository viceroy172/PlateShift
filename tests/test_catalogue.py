from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_convert import MACHINE, make_profiles  # noqa: E402

from plateshift import catalogue  # noqa: E402
from plateshift.profiles import locate  # noqa: E402


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = make_profiles(self.tmp / "OrcaSlicer" / "profiles")
        # a one-nozzle printer too, with no default print profile of its own
        machine = self.root / "Testvendor" / "machine"
        (machine / "Test Mini 0.6 nozzle.json").write_text(json.dumps({
            "name": "Test Mini 0.6 nozzle", "inherits": "fdm_test_common", "instantiation": "true",
            "printer_model": "Test Mini", "nozzle_diameter": ["0.6"], "single_extruder_multi_material": "1"}))
        (self.root / "Custom" / "machine").mkdir(parents=True)

    def test_vendors_and_their_printers(self):
        found = catalogue.vendors([self.root])
        self.assertEqual(list(found), ["Testvendor"])   # "Custom" isn't a printer brand
        printers = {m.name: m for m in catalogue.machines("Testvendor", self.root)}
        self.assertEqual(set(printers), {MACHINE, "Test Mini 0.6 nozzle"})  # not the base preset
        c5p = printers[MACHINE]
        self.assertEqual((c5p.model, c5p.nozzle, c5p.nozzles, c5p.label), ("Test C5P", "0.4", 4, "Test C5P"))
        self.assertEqual(c5p.slicer, "OrcaSlicer")
        mini = printers["Test Mini 0.6 nozzle"]
        self.assertEqual((mini.nozzles, mini.label), (1, "Test Mini 0.6 nozzle"))
        self.assertTrue(mini.default_process.endswith("mm Standard @T") or mini.default_process == "")

    def test_targets_from_printers(self):
        printers = {m.name: m for m in catalogue.machines("Testvendor", self.root)}
        four = catalogue.make_target(printers[MACHINE])
        self.assertEqual((four.label, four.toolheads, four.slot_word, four.suffix),
                         ("Testvendor Test C5P", 4, "Toolhead", "Test C5P"))
        self.assertEqual(four.slicers, ("OrcaSlicer",))
        mini = catalogue.make_target(printers["Test Mini 0.6 nozzle"], 4)
        self.assertEqual((mini.label, mini.toolheads, mini.slot_word),
                         ("Testvendor Test Mini 0.6 nozzle (4 slots)", 4, "Slot"))
        self.assertIn("colour unit", mini.note)
        # saved and read back, and its profiles are found like a built-in printer's
        again = catalogue.from_dict(json.loads(json.dumps(catalogue.to_dict(mini))))
        self.assertEqual(again, mini)
        located, root = locate(again, [self.root])
        self.assertEqual(root, self.root)
        self.assertIsNone(catalogue.from_dict({"key": "broken"}))

    def test_slicer_of_folder(self):
        self.assertEqual(catalogue.slicer_of(Path(r"C:\Program Files\Creality\Creality Print 7.0\x")),
                         "Creality Print")
        self.assertEqual(catalogue.slicer_of(Path(r"C:\Users\a\AppData\Roaming\Orca-Flashforge\system")),
                         "Flash Studio")
        self.assertEqual(catalogue.slicer_of(Path("/home/a/.config/OrcaSlicer/system")), "OrcaSlicer")


if __name__ == "__main__":
    unittest.main()
