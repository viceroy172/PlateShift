from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plateshift import __main__ as entry  # noqa: E402
from plateshift.cli import build_parser  # noqa: E402


class LauncherTests(unittest.TestCase):
    def test_every_command_goes_to_the_command_line(self):
        # a missing one opens the window instead and waits forever
        sub = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
        self.assertEqual(set(sub.choices), entry.COMMANDS - {"-h", "--help"})


if __name__ == "__main__":
    unittest.main()
