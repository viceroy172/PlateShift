from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plateshift.condition import ConditionError, evaluate  # noqa: E402

C5P = {"printer_model": "Flashforge Creator 5 Pro", "printer_variant": "0.4",
       "nozzle_diameter": ["0.4", "0.4", "0.4", "0.4"], "printer_notes": "PRINTER_MODEL_C5P\n"}


class ConditionTests(unittest.TestCase):
    def test_the_condition_flashforge_actually_uses(self):
        cond = ('not ((printer_model == "Flashforge Creator 5" or printer_model == "Flashforge Creator 5 Pro") '
                'and printer_variant == "0.25")')
        self.assertTrue(evaluate(cond, C5P))
        self.assertFalse(evaluate(cond, dict(C5P, printer_variant="0.25")))
        self.assertTrue(evaluate(cond, dict(C5P, printer_model="Flashforge AD5X", printer_variant="0.25")))

    def test_numbers_indexes_and_regex(self):
        self.assertTrue(evaluate("nozzle_diameter[0] == 0.4", C5P))
        self.assertFalse(evaluate("nozzle_diameter[0] > 0.5", C5P))
        self.assertTrue(evaluate("printer_notes =~ /.*C5P.*/", C5P))
        self.assertFalse(evaluate('printer_notes =~ /.*AD5M.*/ and printer_variant == "0.4"', C5P))

    def test_empty_means_any_printer(self):
        self.assertTrue(evaluate("", C5P))
        self.assertTrue(evaluate("   ", C5P))

    def test_unreadable_conditions_raise(self):
        for bad in ('printer_model == ', 'printer_model === "x"', '(printer_variant == "0.4"',
                    'printer_notes =~ "not a regex"', '__import__("os")'):
            with self.subTest(bad), self.assertRaises(ConditionError):
                evaluate(bad, C5P)


if __name__ == "__main__":
    unittest.main()
