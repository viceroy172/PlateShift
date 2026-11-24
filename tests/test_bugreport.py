from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
import urllib.parse
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plateshift import REPO_URL, __version__, bugreport  # noqa: E402


class BugReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_zip_holds_files_log_and_details(self):
        a = self.tmp / "Robot.3mf"
        a.write_bytes(b"source")
        b = self.tmp / "Robot - C5P.3mf"
        b.write_bytes(b"converted")
        about = bugreport.details("Flashforge Creator 5 Pro", {"OrcaSlicer": "C:/orca.exe"})
        self.assertIn(f"PlateShift {__version__}", about)
        self.assertIn("Printer: Flashforge Creator 5 Pro", about)
        path = bugreport.make(self.tmp, [a, b, a, self.tmp / "missing.3mf"], "the log", about, {"printer": "x"})
        self.assertTrue(path.name.startswith("PlateShift problem report "))
        with zipfile.ZipFile(path) as z:
            self.assertEqual(sorted(z.namelist()), ["about.txt", "files/Robot - C5P.3mf", "files/Robot.3mf",
                                                    "log.txt", "settings.json"])
            self.assertEqual(z.read("files/Robot.3mf"), b"source")
            self.assertEqual(json.loads(z.read("settings.json")), {"printer": "x"})
        again = bugreport.make(self.tmp, [], "", about)
        self.assertNotEqual(again, path)  # never replaces an earlier report

    def test_big_files_are_left_out(self):
        big = self.tmp / "huge.3mf"
        big.write_bytes(b"x" * 20)
        saved = bugreport.MAX_FILE
        bugreport.MAX_FILE = 10
        try:
            path = bugreport.make(self.tmp, [big], "", "")
        finally:
            bugreport.MAX_FILE = saved
        with zipfile.ZipFile(path) as z:
            self.assertNotIn("files/huge.3mf", z.namelist())
            self.assertIn("huge.3mf", z.read("left out.txt").decode())

    def test_issue_link(self):
        url = bugreport.issue_url("Problem: ", "PlateShift 9.9")
        self.assertTrue(url.startswith(f"{REPO_URL}/issues/new?"))
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(query["title"], ["Problem: "])
        self.assertIn("PlateShift 9.9", query["body"][0])
        self.assertIn("drag the PlateShift problem report zip", query["body"][0])


if __name__ == "__main__":
    unittest.main()
