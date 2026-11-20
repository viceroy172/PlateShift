from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plateshift.testslice import SliceResult, _seconds, _why  # noqa: E402


class WhyTests(unittest.TestCase):
    def test_skips_noise_for_the_real_reason(self):
        log = "\n".join([
            "Error: Potentially lost branch!, critical: 1",
            "Error: Not precalculated Placeable areas requested, radius 0, layer 0, critical: 0",
            "FF_CRASH_TRACE PlaceholderParser::process_macro end, ctx=0000, output_len=0, error_len=89",
            "gcode path conflicts found between WipeTower and Object_1",
            "plate 1: found slicing result conflict!",
            "Slic3r::CLI::run found error, exit",
        ])
        self.assertEqual(_why(log), "gcode path conflicts found between WipeTower and Object_1")

    def test_joins_a_message_split_over_two_lines(self):
        log = "[2026-11-20 10:00:00] [0x1] [error] retraction_distances_when_cut: \n30 not in range [10, 18]"
        self.assertEqual(_why(log), "retraction_distances_when_cut: 30 not in range [10, 18]")

    def test_falls_back_to_the_last_line(self):
        self.assertEqual(_why("starting\nstopped"), "stopped")
        self.assertEqual(_why(""), "no output")


class ResultTests(unittest.TestCase):
    def test_time_and_weight(self):
        self.assertEqual(_seconds("1d 2h 3m 4s"), 93784)
        self.assertEqual(SliceResult(True, 1, 3 * 3600 + 5 * 60, 41.4).line(), "Test slice OK: 3 h 5 min, 41 g")
        self.assertEqual(SliceResult(True, 2, 600, 10).line(), "Test slice OK: 10 min, 10 g, 2 plates")
        self.assertEqual(SliceResult(False, error="nope").line(), "Test slice failed: nope")


if __name__ == "__main__":
    unittest.main()
