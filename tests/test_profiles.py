from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plateshift.profiles import ProfileLibrary  # noqa: E402


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class ParentLookupTests(unittest.TestCase):
    """the vendor and the shared filament library can both have a base preset by the
    same name; a preset's parent is the one beside it, as in the slicers. found the hard
    way: flashforge's fdm_filament_common says 2.85 mm, so a C5P print came out with
    a third of the plastic it needed"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        vendor = self.root / "Vendor" / "filament"
        library = self.root / "OrcaFilamentLibrary" / "filament"
        _write(vendor / "fdm_filament_common.json",
               {"name": "fdm_filament_common", "filament_diameter": ["2.85"], "nozzle_temperature": ["200"]})
        _write(vendor / "fdm_filament_pla.json",
               {"name": "fdm_filament_pla", "inherits": "fdm_filament_common", "hot_plate_temp": ["60"]})
        _write(library / "base" / "fdm_filament_common.json",
               {"name": "fdm_filament_common", "filament_diameter": ["1.75"], "nozzle_temperature": ["210"]})
        _write(library / "base" / "fdm_filament_pla.json",
               {"name": "fdm_filament_pla", "inherits": "fdm_filament_common", "hot_plate_temp": ["55"]})
        _write(library / "Generic PLA @System.json",
               {"name": "Generic PLA @System", "inherits": "fdm_filament_pla", "instantiation": "false"})
        _write(vendor / "Generic PLA @Vendor.json",
               {"name": "Generic PLA @Vendor", "inherits": "Generic PLA @System", "instantiation": "true"})
        _write(vendor / "Vendor PLA Old.json",
               {"name": "Vendor PLA Old", "inherits": "fdm_filament_pla", "instantiation": "true"})

    def test_library_presets_use_the_library_base(self):
        lib = ProfileLibrary(self.root, "Vendor")
        p = lib.resolve("filament", "Generic PLA @Vendor")
        self.assertEqual(p["filament_diameter"], ["1.75"])
        self.assertEqual(p["hot_plate_temp"], ["55"])
        self.assertEqual(p["nozzle_temperature"], ["210"])

    def test_vendor_presets_keep_the_vendor_base(self):
        lib = ProfileLibrary(self.root, "Vendor")
        p = lib.resolve("filament", "Vendor PLA Old")
        self.assertEqual(p["filament_diameter"], ["2.85"])
        self.assertEqual(p["hot_plate_temp"], ["60"])


if __name__ == "__main__":
    unittest.main()
