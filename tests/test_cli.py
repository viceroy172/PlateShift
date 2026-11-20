from __future__ import annotations

import argparse
import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_project import bambu_settings, write_project  # noqa: E402
from test_convert import TARGET, make_profiles  # noqa: E402

from plateshift import __main__ as entry  # noqa: E402
from plateshift import cli  # noqa: E402
from plateshift.cli import build_parser  # noqa: E402
from plateshift.profiles import ProfileLibrary  # noqa: E402


class LauncherTests(unittest.TestCase):
    def test_every_command_goes_to_the_command_line(self):
        # a missing one opens the window instead and waits forever
        sub = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
        self.assertEqual(set(sub.choices), entry.COMMANDS - {"-h", "--help", "--version"})

    def test_version_names_the_release_and_repo(self):
        from plateshift import REPO_URL, __version__
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as done:
            cli.main(["--version"])
        self.assertEqual(done.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), f"PlateShift {__version__} {REPO_URL}")


class ConvertCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        lib = ProfileLibrary(make_profiles(self.tmp / "profiles"), "Testvendor")
        self.saved = cli._library
        cli._library = lambda target, profiles: (TARGET, lib)
        self.addCleanup(setattr, cli, "_library", self.saved)

    def run_cli(self, *args) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_one_bad_file_doesnt_stop_a_batch(self):
        bad = self.tmp / "bad.3mf"
        bad.write_bytes(b"not a zip")
        good = write_project(self.tmp / "good.3mf", bambu_settings(), [[(128, 128)]])
        code, out, err = self.run_cli("convert", str(bad), str(good))
        self.assertEqual(code, 1)
        self.assertIn("bad.3mf", err)
        self.assertTrue((self.tmp / "good - T.3mf").exists())

    def test_existing_output_is_kept_unless_overwrite(self):
        good = write_project(self.tmp / "good.3mf", bambu_settings(), [[(128, 128)]])
        self.run_cli("convert", str(good))
        (self.tmp / "good - T.3mf").write_text("edited")
        self.run_cli("convert", str(good))
        self.assertEqual((self.tmp / "good - T.3mf").read_text(), "edited")
        self.assertTrue((self.tmp / "good - T (2).3mf").exists())
        self.run_cli("convert", str(good), "--overwrite")
        self.assertNotEqual((self.tmp / "good - T.3mf").read_bytes(), b"edited")

    def test_one_output_file_for_several_inputs_is_refused(self):
        a = write_project(self.tmp / "a.3mf", bambu_settings(), [[(128, 128)]])
        b = write_project(self.tmp / "b.3mf", bambu_settings(), [[(128, 128)]])
        code, _, err = self.run_cli("convert", str(a), str(b), "-o", str(self.tmp / "out.3mf"))
        self.assertEqual(code, 2)
        self.assertIn("use a folder", err)
        self.assertFalse((self.tmp / "out.3mf").exists())

    def test_inspect_lists_colours_and_presets(self):
        good = write_project(self.tmp / "good.3mf", bambu_settings(), [[(128, 128)]])
        code, out, _ = self.run_cli("inspect", str(good))
        self.assertEqual(code, 0)
        self.assertIn("4 colours", out)
        self.assertIn("toolhead 3: Generic PETG @T", out)


if __name__ == "__main__":
    unittest.main()
