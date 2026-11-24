from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plateshift.convert import SlotMapping  # noqa: E402
from plateshift.price import cost_lines, money, parse_price, regional_currency  # noqa: E402
from plateshift.testslice import SliceResult, _add, _run  # noqa: E402

SLOTS = [SlotMapping(1, "#000000", "", "PLA", "Generic PLA @T"),
         SlotMapping(2, "#FFFFFF", "", "PLA", "Generic PLA @T"),
         SlotMapping(3, "#FF8000", "", "PETG", "Generic PETG @T")]


class PriceTests(unittest.TestCase):
    def test_cost_per_slot_and_total(self):
        lines = cost_lines([25.37, 0.0, 22.8], SLOTS, {1: 350, 2: 350, 3: 399.5}, "R")
        self.assertEqual(lines, ["Filament cost: R 17.99",
                                 "  Toolhead 1: 25.4 g at R 350.00/kg = R 8.88",
                                 "  Toolhead 3: 22.8 g at R 399.50/kg = R 9.11"])

    def test_missing_prices_are_named(self):
        lines = cost_lines([10, 0, 5], SLOTS, {1: 300, 2: 300, 3: 0}, "$", hint="add one")
        self.assertEqual(lines[0], "Filament cost: $3.00 so far")
        self.assertEqual(lines[-1], "  No price for toolhead 3 (Generic PETG @T); add one")
        self.assertEqual(cost_lines([10], SLOTS, {}, "$", "Filament", "add one"),
                         ["No price for filament 1 (Generic PLA @T); add one"])
        self.assertEqual(cost_lines([], SLOTS, {}, "$"), [])

    def test_money_and_parsing(self):
        self.assertEqual(money(1234.5, "$"), "$1,234.50")
        self.assertEqual(money(12.5, "R"), "R 12.50")
        self.assertEqual(money(3, "CHF"), "CHF 3.00")
        self.assertEqual(parse_price("R 349,99"), 349.99)
        self.assertEqual(parse_price("350"), 350.0)
        for bad in ("", "abc", "1.2.3"):
            self.assertIsNone(parse_price(bad))

    def test_regional_currency_is_something(self):
        symbol = regional_currency()
        self.assertTrue(symbol and symbol == symbol.strip())

    def test_grams_per_filament_add_up_over_plates(self):
        totals: list[float] = []
        _add(totals, " 25.37, 0.00, 22.80, 0.00")
        _add(totals, "1.0, 2.0")
        self.assertEqual(totals, [26.37, 2.0, 22.8, 0.0])

    def test_slice_result_reads_each_plate(self):
        # a stand-in slicer that writes two plates of g-code
        tmp = Path(tempfile.mkdtemp())
        script = tmp / "fake_slicer.py"
        script.write_text(
            "import sys, pathlib\n"
            "out = pathlib.Path(sys.argv[sys.argv.index('--outputdir') + 1])\n"
            "for n, g in ((1, '10.00, 2.50'), (2, '5.00, 0.00')):\n"
            "    (out / f'plate_{n}.gcode').write_text(\n"
            "        f'; filament used [g] = {g}\\n; total filament used [g] = 1\\n'\n"
            "        '; estimated printing time (normal mode) = 1h 2m 3s\\n')\n", encoding="utf-8")

        class Fake:
            def command(self, *args):
                return [sys.executable, str(script), *args]
        result = _run(Fake(), tmp / "x.3mf", 60)
        self.assertIsInstance(result, SliceResult)
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.plates, 2)
        self.assertEqual(result.grams_each, [15.0, 2.5])


if __name__ == "__main__":
    unittest.main()
