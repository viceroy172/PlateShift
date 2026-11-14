from __future__ import annotations

import plistlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plateshift import slicers  # noqa: E402


def fake_app(folder: Path, name: str, exe: str, plist: bool = True) -> Path:
    app = folder / f"{name}.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos / exe).write_text("#!/bin/sh\n")
    (macos / exe).chmod(0o755)
    if plist:
        with open(app / "Contents" / "Info.plist", "wb") as fh:
            plistlib.dump({"CFBundleExecutable": exe, "CFBundleName": name}, fh)
    return app


class MacAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_finds_slicer_apps_and_their_programs(self):
        orca = fake_app(self.tmp, "OrcaSlicer", "OrcaSlicer")
        creality = fake_app(self.tmp, "Creality Print", "CrealityPrint")
        other = fake_app(self.tmp, "Bambu Studio", "BambuStudio")
        found = {s.label: s for s in slicers._mac([other, creality, orca])}
        self.assertEqual(set(found), {"OrcaSlicer", "Creality Print"})
        self.assertEqual(found["OrcaSlicer"].exe, orca / "Contents" / "MacOS" / "OrcaSlicer")
        self.assertEqual(found["OrcaSlicer"].app, orca)
        self.assertEqual(found["Creality Print"].command("--slice", "0")[1:], ["--slice", "0"])

    def test_program_found_without_info_plist(self):
        app = fake_app(self.tmp, "Flash Studio", "flash-studio", plist=False)
        if sys.platform == "win32":
            self.skipTest("no executable bit on windows")
        self.assertEqual(slicers.bundle_exe(app), app / "Contents" / "MacOS" / "flash-studio")

    def test_test_slicer_prefers_flash_studio(self):
        orca = slicers.Slicer("OrcaSlicer", Path("orca"))
        flash = slicers.Slicer("Flash Studio", Path("flash"))
        self.assertIs(slicers.test_slicer({"OrcaSlicer": orca, "Flash Studio": flash}), flash)
        self.assertIs(slicers.test_slicer({"OrcaSlicer": orca}), orca)
        self.assertIsNone(slicers.test_slicer({}))


if __name__ == "__main__":
    unittest.main()
