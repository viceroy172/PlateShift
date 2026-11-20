from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plateshift.printers import BY_KEY, TARGETS  # noqa: E402


class PrinterListTests(unittest.TestCase):
    def test_keys_and_file_suffixes_are_unique(self):
        self.assertEqual(len(BY_KEY), len(TARGETS))
        self.assertEqual(len({t.suffix for t in TARGETS}), len(TARGETS))
        self.assertEqual(len({t.label for t in TARGETS}), len(TARGETS))

    def test_every_printer_is_described_sensibly(self):
        for t in TARGETS:
            with self.subTest(t.key):
                self.assertIn(t.toolheads, (1, 4))
                self.assertTrue(t.machine and t.default_process and t.vendor)
                for machine, process in t.preset_names():
                    self.assertIn("0.4", machine)
                    self.assertTrue(process.startswith("0.20mm"))
                self.assertTrue(set(t.slicers) <= {"Flash Studio", "OrcaSlicer", "Creality Print"})
                if t.toolheads == 1:
                    self.assertIn("single colour", t.label)

    def test_one_nozzle_colour_printers_say_slot(self):
        for key in ("ad5x", "k1cfs", "k1maxcfs", "k1max2025cfs", "k2plus", "sparkxi7", "sparkxi8"):
            self.assertEqual(BY_KEY[key].slot_word, "Slot", key)
        self.assertEqual(BY_KEY["c5p"].slot_word, "Toolhead")
        self.assertEqual(BY_KEY["k1"].slot_word, "Filament")


if __name__ == "__main__":
    unittest.main()
