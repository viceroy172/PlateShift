from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_project import bambu_settings, write_project  # noqa: E402
from test_convert import MACHINE, make_profiles, read  # noqa: E402

from plateshift.convert import (ConversionError, convert, parse_plates, plate_names,  # noqa: E402
                                plate_output)
from plateshift.printers import Target  # noqa: E402
from plateshift.profiles import ProfileLibrary  # noqa: E402

TARGET = Target("test", "Test C5P", "Testvendor", MACHINE, "0.20mm Standard @T", 4, "C5P")
STEP = 256 * 1.2  # plate pitch on a 256 mm bed
# where a cube centred on each of 4 plates sits (2 columns)
CENTRES = [(128, 128), (128 + STEP, 128), (128, 128 - STEP), (128 + STEP, 128 - STEP)]


def _positions(path: Path) -> list[tuple[str, float, float]]:
    model = read(path, "3D/3dmodel.model")
    out = []
    for obj, t in re.findall(r'<item objectid="(\d+)" transform="([^"]+)"', model):
        n = t.split()
        out.append((obj, round(float(n[9]), 3), round(float(n[10]), 3)))
    return out


class PlateChoiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.lib = ProfileLibrary(make_profiles(self.tmp / "profiles"), "Testvendor")
        settings = bambu_settings(wipe_tower_x=["10", "20", "30", "40"], wipe_tower_y=["11", "21", "31", "41"])
        self.src = write_project(self.tmp / "in.3mf", settings, [[c] for c in CENTRES], gcode=True)
        # what bambu studio adds per plate
        with zipfile.ZipFile(self.src, "a") as z:
            for i in range(1, 5):
                for name in (f"plate_{i}.png", f"plate_{i}_small.png", f"plate_no_light_{i}.png",
                             f"top_{i}.png", f"pick_{i}.png", f"plate_{i}.json"):
                    z.writestr(f"Metadata/{name}", f"{name}")
            z.writestr("Metadata/filament_sequence.json",
                       json.dumps({f"plate_{i}": {"sequence": [i]} for i in range(1, 5)}))
            z.writestr("Metadata/slice_info.config",
                       '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <header>\n  </header>\n'
                       + "".join(f'  <plate>\n    <metadata key="index" value="{i}"/>\n  </plate>\n'
                                 for i in range(1, 5)) + "</config>\n")
        self.out = self.tmp / "out.3mf"

    def test_keeps_chosen_plates_renumbered_and_placed(self):
        report = convert(self.src, self.out, TARGET, self.lib, plates=[2, 4])
        # plate 2's cube is now on plate 1, plate 4's on plate 2 (2 plates still make 2 columns)
        self.assertEqual(_positions(self.out), [("2", 128, 128), ("4", 128 + STEP, 128)])
        model = read(self.out, "3D/3dmodel.model")
        self.assertNotIn('<object id="1"', model)
        self.assertNotIn('<object id="3"', model)

        ms = read(self.out, "Metadata/model_settings.config")
        self.assertEqual(re.findall(r'key="plater_id" value="(\d+)"', ms), ["1", "2"])
        self.assertEqual(re.findall(r'<object id="(\d+)"', ms), ["2", "4"])
        self.assertIn('<metadata key="object_id" value="4"/>', ms.split("</plate>")[1])

        with zipfile.ZipFile(self.out) as z:
            names = set(z.namelist())
            self.assertEqual(z.read("Metadata/plate_1.png"), b"plate_2.png")
            self.assertEqual(z.read("Metadata/pick_2.png"), b"pick_4.png")
            self.assertEqual(z.read("Metadata/plate_no_light_1.png"), b"plate_no_light_2.png")
        self.assertFalse([n for n in names if re.search(r"_[34](_small)?\.(png|json)$", n)])

        s = json.loads(read(self.out, "Metadata/project_settings.config"))
        self.assertEqual(s["wipe_tower_x"], ["20", "40"])
        self.assertEqual(s["wipe_tower_y"], ["21", "41"])
        self.assertEqual(json.loads(read(self.out, "Metadata/filament_sequence.json")),
                         {"plate_1": {"sequence": [2]}, "plate_2": {"sequence": [4]}})
        info = read(self.out, "Metadata/slice_info.config")
        self.assertEqual(re.findall(r'key="index" value="(\d+)"', info), ["1", "2"])
        self.assertEqual(report.plates, [2, 4])
        self.assertIn("  Kept plates 2, 4 of 4 (now plates 1-2)", report.lines())

    def test_all_plates_or_none_changes_nothing(self):
        for plates in (None, [1, 2, 3, 4]):
            report = convert(self.src, self.out, TARGET, self.lib, plates=plates)
            self.assertEqual(report.plates, [])
            self.assertEqual(len(_positions(self.out)), 4)

    def test_one_plate(self):
        report = convert(self.src, self.out, TARGET, self.lib, plates=[3])
        self.assertEqual(_positions(self.out), [("3", 128, 128)])
        self.assertIn("  Kept plate 3 of 4 (now plate 1)", report.lines())

    def test_no_such_plate(self):
        with self.assertRaisesRegex(ConversionError, "4 plates; there is no plate 5"):
            convert(self.src, self.out, TARGET, self.lib, plates=[1, 5])
        self.assertFalse(self.out.exists())

    def test_names_parsing_and_outputs(self):
        self.assertEqual(plate_names(self.src), ["Plate 1", "Plate 2", "Plate 3", "Plate 4"])
        self.assertEqual(parse_plates("1, 3-4,3"), [1, 3, 4])
        for bad in ("0", "4-2", "x", ""):
            with self.assertRaises(ValueError):
                parse_plates(bad)
        self.assertEqual(plate_output(Path("a/Robot - C5P.3mf"), 2), Path("a/Robot - C5P - plate 2.3mf"))


class ComponentTests(unittest.TestCase):
    """bambu keeps meshes in 3D/Objects/*.model; files only dropped objects use go too"""

    def test_unused_object_files_are_dropped(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        lib = ProfileLibrary(make_profiles(tmp / "profiles"), "Testvendor")
        src = write_project(tmp / "in.3mf", bambu_settings(), [[CENTRES[0]], [CENTRES[1]]])
        # turn both cubes into component objects with their meshes in separate files
        with zipfile.ZipFile(src) as z:
            entries = {n: z.read(n) for n in z.namelist()}
        model = entries["3D/3dmodel.model"].decode()
        meshes = re.findall(r'<object id="(\d+)" type="model">(.*?)</object>', model, re.S)
        for obj, mesh in meshes:
            entries[f"3D/Objects/o{obj}.model"] = (
                f'<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources>'
                f'<object id="{obj}" type="model">{mesh}</object></resources></model>').encode()
            model = model.replace(
                f'<object id="{obj}" type="model">{mesh}</object>',
                f'<object id="{obj}" type="model"><components><component p:path="/3D/Objects/o{obj}.model" '
                f'objectid="{obj}"/></components></object>')
        entries["3D/3dmodel.model"] = model.encode()
        entries["3D/_rels/3dmodel.model.rels"] = (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            ' <Relationship Target="/3D/Objects/o1.model" Id="rel-1" Type="x"/>\n'
            ' <Relationship Target="/3D/Objects/o2.model" Id="rel-2" Type="x"/>\n'
            '</Relationships>\n').encode()
        with zipfile.ZipFile(src, "w") as z:
            for n, data in entries.items():
                z.writestr(n, data)

        out = tmp / "out.3mf"
        convert(src, out, TARGET, lib, plates=[2])
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
        self.assertIn("3D/Objects/o2.model", names)
        self.assertNotIn("3D/Objects/o1.model", names)
        rels = read(out, "3D/_rels/3dmodel.model.rels")
        self.assertIn("o2.model", rels)
        self.assertNotIn("o1.model", rels)


if __name__ == "__main__":
    unittest.main()
