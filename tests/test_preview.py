from __future__ import annotations

import base64
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plateshift.preview import plate_pictures  # noqa: E402

# a 1 x 1 pixel png
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==")


class PlatePictureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def project(self, entries: dict[str, bytes]) -> Path:
        path = self.tmp / "p.3mf"
        with zipfile.ZipFile(path, "w") as z:
            for name, data in entries.items():
                z.writestr(name, data)
        return path

    def test_every_plate_in_order(self):
        path = self.project({"Metadata/plate_10.png": PNG + b"10", "Metadata/plate_2.png": PNG + b"2",
                             "Metadata/plate_1.png": PNG + b"1", "Metadata/plate_1_small.png": PNG,
                             "Metadata/top_1.png": PNG})
        self.assertEqual(plate_pictures(path), [PNG + b"1", PNG + b"2", PNG + b"10"])

    def test_single_thumbnail_when_there_are_no_plate_pictures(self):
        self.assertEqual(plate_pictures(self.project({"Metadata/thumbnail.png": PNG})), [PNG])

    def test_no_picture_is_no_preview(self):
        self.assertEqual(plate_pictures(self.project({"3D/3dmodel.model": b"<model/>"})), [])
        self.assertEqual(plate_pictures(self.project({"Metadata/plate_1.png": b"not a png"})), [])
        bad = self.tmp / "bad.3mf"
        bad.write_bytes(b"not a zip")
        self.assertEqual(plate_pictures(bad), [])
        self.assertEqual(plate_pictures(self.tmp / "missing.3mf"), [])


if __name__ == "__main__":
    unittest.main()
