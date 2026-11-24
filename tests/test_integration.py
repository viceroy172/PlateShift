from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plateshift import integration  # noqa: E402

SUFFIXES = ["C5P", "K1 Max", "AD5X"]


class ConvertedNameTests(unittest.TestCase):
    def test_only_plateshift_names(self):
        for name in ("Robot - C5P.3mf", "Robot - K1 Max - plate 2.3mf", "Robot - AD5X (2).3mf"):
            self.assertTrue(integration.is_converted(Path(name), SUFFIXES), name)
        for name in ("Robot.3mf", "Mini - Bambu edition.3mf", "Robot - C5P notes.3mf"):
            self.assertFalse(integration.is_converted(Path(name), SUFFIXES), name)
        self.assertFalse(integration.is_converted(Path("Robot - C5P.3mf"), []))


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.old = self.tmp / "old.3mf"
        self.old.write_bytes(b"there before")
        os.utime(self.old, (1, 1))   # long ago

    def test_new_finished_downloads_only(self):
        watcher = integration.DownloadWatcher(self.tmp, SUFFIXES)
        self.assertEqual(watcher.check(), [])
        new = self.tmp / "Dragon.3mf"
        new.write_bytes(b"part")
        (self.tmp / "Dragon - C5P.3mf").write_bytes(b"ours")
        (self.tmp / "notes.txt").write_text("x")
        self.assertEqual(watcher.check(), [])          # first sighting
        new.write_bytes(b"part and more")
        self.assertEqual(watcher.check(), [])          # still growing
        self.assertEqual(watcher.check(), [new])       # the same size twice: done
        self.assertEqual(watcher.check(), [])          # offered once

    def test_missing_folder(self):
        self.assertEqual(integration.DownloadWatcher(self.tmp / "nope").check(), [])

    def test_downloads_folder_exists_somewhere(self):
        self.assertTrue(str(integration.downloads_folder()))


class LaunchTests(unittest.TestCase):
    def test_source_run_says_where_plateshift_is(self):
        command = integration.launch_command()
        if sys.platform == "win32":
            self.assertEqual(command[1], "-c")
            self.assertIn(repr(str(Path(__file__).resolve().parents[1])), command[2])
        else:
            self.assertTrue(command[0].endswith("plateshift.sh"))


@unittest.skipIf(sys.platform == "win32" or sys.platform == "darwin", "desktop entries are for linux")
class DesktopEntryTests(unittest.TestCase):
    def test_add_and_remove(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(tmp)}):
            self.assertFalse(integration.has_open_with())
            integration.add_open_with()
            entry = (tmp / "applications" / "plateshift.desktop").read_text()
            self.assertIn("MimeType=model/3mf;", entry)
            self.assertIn("%F", entry)
            self.assertTrue((tmp / "icons" / "hicolor").is_dir())
            self.assertTrue(integration.has_open_with())
            integration.remove_open_with()
            self.assertFalse(integration.has_open_with())


if __name__ == "__main__":
    unittest.main()
