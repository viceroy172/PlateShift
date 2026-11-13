from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plateshift import paint  # noqa: E402

# codes seen in a real bambu studio 2.9 project
REAL = ["4", "8", "0C", "1C", "0C0C40C3", "440C6", "40C4140C5A", "40C4430C0C0C40C30C0C40C33",
        "0C0C4A40C440C23", "0C40C4540C13"]


class PaintTests(unittest.TestCase):
    def test_simple_states(self):
        self.assertEqual([paint.decode(c) for c in ("4", "8", "0C", "1C")], [1, 2, 3, 4])

    def test_round_trip_real_codes(self):
        for code in REAL:
            self.assertEqual(paint.encode(paint.decode(code)), code)

    def test_round_trip_large_states(self):
        for state in range(40):
            self.assertEqual(paint.decode(paint.encode(state)), state)

    def test_remap_split_triangle(self):
        tree = paint.decode("40C4430C0C0C40C30C0C40C33")
        self.assertEqual(paint.states(tree), {1, 3})
        new = paint.remap_tree(tree, {3: 2, 1: 4})
        self.assertEqual(paint.states(new), {2, 4})
        self.assertEqual(paint.decode(paint.encode(new)), new)

    def test_remap_model(self):
        xml = '<triangle v1="0" v2="1" v3="2" paint_color="1C"/><triangle v1="0" v2="1" v3="2" paint_color="4"/>'
        out, n = paint.remap_model(xml, {4: 2})
        self.assertEqual(n, 1)
        self.assertEqual(paint.used_states(out), {2: 1, 1: 1})


if __name__ == "__main__":
    unittest.main()
