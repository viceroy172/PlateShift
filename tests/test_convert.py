from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_project import bambu_settings, write_project  # noqa: E402
from plateshift import paint  # noqa: E402
from plateshift.convert import ConversionError, _plate_centre, _plate_columns, analyse, convert  # noqa: E402
from plateshift.printers import BY_KEY, Target  # noqa: E402
from plateshift.profiles import ProfileLibrary, find_profile_root  # noqa: E402

MACHINE = "Test C5P 0.4 nozzle"
TARGET = Target("t", "Test C5P", "Testvendor", MACHINE, "0.20mm Standard @T", 4, "T")


def _write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def make_profiles(root: Path, bed: int = 256) -> Path:
    v = root / "Testvendor"
    _write(v / "machine" / "fdm_test_common.json", {
        "name": "fdm_test_common", "instantiation": "false",
        "machine_start_gcode": "START_PRINT", "min_layer_height": "0.08", "max_layer_height": "0.32",
    })
    _write(v / "machine" / f"{MACHINE}.json", {
        "name": MACHINE, "inherits": "fdm_test_common", "instantiation": "true",
        "printer_model": "Test C5P", "nozzle_diameter": ["0.4"] * 4,
        "printable_area": ["0x0", f"{bed}x0", f"{bed}x{bed}", f"0x{bed}"],
        "printable_height": "250",
        "default_print_profile": "0.20mm Standard @T",
    })
    for lh in ("0.12", "0.20"):
        _write(v / "process" / f"{lh}mm Standard @T.json", {
            "name": f"{lh}mm Standard @T", "instantiation": "true", "compatible_printers": [MACHINE],
            "layer_height": lh, "wall_loops": "2", "sparse_infill_density": "15%",
            "sparse_infill_pattern": "grid", "outer_wall_speed": "300", "line_width": "0.45",
            "enable_support": "0",
        })
    lib = root / "OrcaFilamentLibrary" / "filament"
    _write(lib / "Generic PLA @System.json", {
        "name": "Generic PLA @System", "instantiation": "false", "filament_type": ["PLA"],
        "nozzle_temperature": ["210"], "filament_vendor": ["Generic"]})
    _write(lib / "Generic PETG @System.json", {
        "name": "Generic PETG @System", "instantiation": "false", "filament_type": ["PETG"],
        "nozzle_temperature": ["240"], "filament_vendor": ["Generic"]})
    fil = v / "filament"
    for name, parent, extra in [
        ("Generic PLA @T", "Generic PLA @System", {}),
        # flashforge uses SILK as the type
        ("Generic PLA Silk @T", "Generic PLA @System", {"nozzle_temperature": ["225"], "filament_type": ["SILK"]}),
        ("Testvendor PLA Basic @T", "Generic PLA @System", {"filament_vendor": ["Testvendor"]}),
        ("Generic PETG @T", "Generic PETG @System", {}),
    ]:
        _write(fil / f"{name}.json", {"name": name, "inherits": parent, "instantiation": "true",
                                       "compatible_printers": [MACHINE], "filament_id": name[:6], **extra})
    _write(fil / "Other printer PLA.json", {"name": "Other printer PLA", "inherits": "Generic PLA @System",
                                            "instantiation": "true", "compatible_printers": ["Someone else"]})
    return root


def read(path: Path, name: str) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read(name).decode("utf-8")


class ConvertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.lib = ProfileLibrary(make_profiles(self.tmp / "profiles"), "Testvendor")

    def convert(self, settings=None, plates=None, gcode=False, **kw):
        src = write_project(self.tmp / "in.3mf", bambu_settings() if settings is None else settings,
                            plates or [[(128, 128)]], gcode=gcode)
        out = self.tmp / "out.3mf"
        report = convert(src, out, TARGET, self.lib, **kw)
        return report, out, json.loads(read(out, "Metadata/project_settings.config"))

    def test_printer_and_process_are_replaced(self):
        report, _, s = self.convert()
        self.assertEqual(s["printer_settings_id"], MACHINE)
        self.assertEqual(s["printer_model"], "Test C5P")
        self.assertEqual(s["machine_start_gcode"], "START_PRINT")
        # 0.16 is equally close to 0.12 and 0.20, default wins
        self.assertEqual(s["print_settings_id"], "0.20mm Standard @T")
        self.assertEqual(report.process, "0.20mm Standard @T")

    def test_design_settings_are_kept_and_speeds_are_not(self):
        _, _, s = self.convert()
        self.assertEqual(s["layer_height"], "0.16")
        self.assertEqual(s["wall_loops"], "3")
        self.assertEqual(s["sparse_infill_density"], "25%")
        self.assertEqual(s["sparse_infill_pattern"], "gyroid")
        self.assertEqual(s["enable_support"], "1")
        self.assertEqual(s["line_width"], "0.42")
        self.assertEqual(s["outer_wall_speed"], "300")
        changed = s["different_settings_to_system"][0].split(";")
        self.assertIn("wall_loops", changed)
        self.assertEqual(len(s["different_settings_to_system"]), 4 + 2)
        self.assertEqual(s["inherits_group"], [""] * 6)

    def test_filaments_match_by_type_and_name_and_keep_colours(self):
        report, _, s = self.convert()
        self.assertEqual(s["filament_settings_id"], [
            "Testvendor PLA Basic @T", "Generic PLA Silk @T", "Generic PETG @T", "Generic PLA @T"])
        self.assertEqual(s["filament_colour"], ["#000000", "#C0C0C0", "#FF8000", "#FFFFFF"])
        self.assertEqual(s["nozzle_temperature"], ["210", "225", "240", "210"])
        self.assertEqual(s["filament_type"], ["PLA", "SILK", "PETG", "PLA"])
        self.assertEqual(len(report.slots), 4)

    def test_filament_override(self):
        _, _, s = self.convert(overrides={1: "Generic PLA Silk @T"})
        self.assertEqual(s["filament_settings_id"][0], "Generic PLA Silk @T")

    def test_unknown_type_falls_back_and_says_so(self):
        settings = bambu_settings(filament_type=["TPU", "PLA", "PLA", "PLA"])
        report, _, s = self.convert(settings)
        self.assertEqual(s["filament_type"][0], "PLA")
        self.assertIn("check it", report.slots[0].note)

    def six_colour_project(self):
        colours = ["#000000", "#101010", "#FFFFFF", "#F0F0F0", "#FF0000", "#00AA00"]
        settings = bambu_settings(filament_type=["PLA"] * 6, filament_colour=colours,
                                  filament_settings_id=["Bambu PLA Basic @BBL X1C"] * 6,
                                  support_filament="6")
        codes = {i: paint.encode(i) for i in range(1, 7)}
        return write_project(self.tmp / "six.3mf", settings, [[(30 + 30 * i, 128) for i in range(6)]],
                             paint=codes)

    def painted_states(self, path):
        with zipfile.ZipFile(path) as z:
            return paint.used_states(z.read("3D/3dmodel.model").decode())

    def test_too_many_colours_are_merged_by_similarity(self):
        src = self.six_colour_project()
        plan = analyse(src, TARGET, self.lib)
        self.assertEqual(plan.merge, [1, 1, 2, 2, 3, 4])
        report = convert(src, self.tmp / "six-out.3mf", TARGET, self.lib)
        s = json.loads(read(self.tmp / "six-out.3mf", "Metadata/project_settings.config"))
        self.assertEqual(len(s["filament_settings_id"]), 4)
        self.assertEqual(len(s["nozzle_temperature"]), 4)
        self.assertEqual(s["support_filament"], "4")
        self.assertEqual(sorted(self.painted_states(self.tmp / "six-out.3mf")), [1, 2, 3, 4])
        self.assertEqual(report.slots[0].sources, [1, 2])
        self.assertGreater(report.repainted, 0)
        ms = read(self.tmp / "six-out.3mf", "Metadata/model_settings.config")
        # objects were on filaments 1,2,3,4,1,2 -> 1,1,2,2,1,1
        self.assertEqual(sorted(set(re.findall(r'key="extruder" value="(\d+)"', ms))), ["1", "2"])

    def test_merge_can_be_chosen(self):
        src = self.six_colour_project()
        convert(src, self.tmp / "pick.3mf", TARGET, self.lib, merge=[4, 3, 2, 1, 1, 1])
        self.assertEqual(sorted(self.painted_states(self.tmp / "pick.3mf")), [1, 2, 3, 4])
        s = json.loads(read(self.tmp / "pick.3mf", "Metadata/project_settings.config"))
        self.assertEqual(s["filament_colour"][3], "#000000")

    def test_single_head_printer_gets_one_filament(self):
        single = Target("s", "Single", "Testvendor", MACHINE, "0.20mm Standard @T", 1, "S")
        report = convert(self.six_colour_project(), self.tmp / "one.3mf", single, self.lib)
        s = json.loads(read(self.tmp / "one.3mf", "Metadata/project_settings.config"))
        self.assertEqual(len(s["filament_settings_id"]), 1)
        self.assertEqual(sorted(self.painted_states(self.tmp / "one.3mf")), [1])
        self.assertIn("one filament", " ".join(report.warnings))
        self.assertEqual(report.slot_word, "Filament")

    def test_source_printer_gcode_never_leaks(self):
        settings = bambu_settings(time_lapse_gcode="M971 S11 C10 O0", wrapping_detection_gcode="M1009")
        _, _, s = self.convert(settings)
        self.assertNotIn("time_lapse_gcode", s)
        self.assertNotIn("wrapping_detection_gcode", s)
        self.assertEqual(s["machine_start_gcode"], "START_PRINT")

    def test_bambu_only_options_are_dropped(self):
        # support_object_skip_flush=1 broke slicing for a single-nozzle printer
        _, _, s = self.convert(bambu_settings(support_object_skip_flush="1", ironing_angle="45"))
        self.assertNotIn("support_object_skip_flush", s)
        self.assertNotIn("ironing_angle", s)
        self.assertEqual(s["wall_loops"], "3")  # design choices still kept

    def test_toolhead_colours_can_be_changed(self):
        report, _, s = self.convert(colours={1: "#2e7d32", 3: "A81608"})
        self.assertEqual(s["filament_colour"], ["#2E7D32", "#C0C0C0", "#A81608", "#FFFFFF"])
        self.assertIn("colour was #000000", report.slots[0].note)
        self.assertEqual(report.slots[1].note, "")
        with self.assertRaisesRegex(ConversionError, "isn't a colour"):
            self.convert(colours={2: "green"})

    def test_unpainted_projects_are_not_rewritten(self):
        report, out, _ = self.convert()
        self.assertEqual(report.repainted, 0)

    def test_user_presets_are_offered_and_used(self):
        user = self.tmp / "user"
        _write(user / "filament" / "My SBS.json", {
            "name": "My SBS", "inherits": "Generic PLA @T", "from": "User",
            "nozzle_temperature": ["235"], "filament_type": ["PLA"]})
        lib = ProfileLibrary(self.tmp / "profiles", "Testvendor", [("OrcaSlicer", user)])
        names = [p["name"] for p in lib.compatible("filament", MACHINE, include_user=True)]
        self.assertIn("My SBS", names)
        src = write_project(self.tmp / "u.3mf", bambu_settings(), [[(128, 128)]])
        report = convert(src, self.tmp / "u-out.3mf", TARGET, lib, overrides={2: "My SBS"})
        s = json.loads(read(self.tmp / "u-out.3mf", "Metadata/project_settings.config"))
        self.assertEqual(s["filament_settings_id"][1], "My SBS")
        self.assertEqual(s["nozzle_temperature"][1], "235")
        self.assertEqual(s["inherits_group"][2], "Generic PLA @T")
        # auto choice never picks user presets
        self.assertNotIn("My SBS", s["filament_settings_id"][0])

    def test_objects_off_the_bed_are_reported(self):
        src = write_project(self.tmp / "big.3mf", bambu_settings(), [[(128, 128), (250, 128)]], size=20)
        report = convert(src, self.tmp / "big-out.3mf", TARGET, self.lib)
        self.assertEqual(len([w for w in report.warnings if "sticks out" in w]), 1)
        tall = write_project(self.tmp / "tall.3mf", bambu_settings(), [[(100, 100)]], size=300)
        report = convert(tall, self.tmp / "tall-out.3mf", TARGET, self.lib)
        self.assertTrue(any("mm tall" in w for w in report.warnings), report.warnings)

    def test_bare_model_is_refused(self):
        src = write_project(self.tmp / "bare.3mf", None, [[(10, 10)]])
        with self.assertRaisesRegex(ConversionError, "bare model"):
            convert(src, self.tmp / "o.3mf", TARGET, self.lib)

    def test_project_already_for_the_target_is_refused(self):
        src = write_project(self.tmp / "done.3mf", bambu_settings(printer_settings_id=MACHINE), [[(10, 10)]])
        with self.assertRaisesRegex(ConversionError, "already set up"):
            convert(src, self.tmp / "o.3mf", TARGET, self.lib)

    def test_does_not_overwrite_source(self):
        src = write_project(self.tmp / "same.3mf", bambu_settings(), [[(10, 10)]])
        with self.assertRaises(ConversionError):
            convert(src, src, TARGET, self.lib)

    def test_sliced_gcode_is_removed(self):
        report, out, _ = self.convert(gcode=True, plates=[[(128, 128)], [(400, 128)]])
        self.assertTrue(report.removed_gcode)
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
        self.assertFalse([n for n in names if n.endswith(".gcode") or n.endswith(".md5")])
        self.assertNotIn("Metadata/_rels/model_settings.config.rels", names)
        self.assertNotIn("plate_1.gcode", read(out, "Metadata/model_settings.config"))

    def test_meshes_are_copied_untouched(self):
        _, out, _ = self.convert()
        src = self.tmp / "in.3mf"
        self.assertEqual(re.findall(r"<mesh>.*?</mesh>", read(src, "3D/3dmodel.model"), re.S),
                         re.findall(r"<mesh>.*?</mesh>", read(out, "3D/3dmodel.model"), re.S))

    def test_same_bed_size_moves_nothing(self):
        report, out, _ = self.convert(plates=[[(100, 120)]])
        self.assertEqual(report.moved_objects, 0)
        self.assertIn('transform="1 0 0 0 1 0 0 0 1 100 120 5"', read(out, "3D/3dmodel.model"))

    def test_smaller_source_bed_objects_stay_centred_on_their_plates(self):
        # 180mm bed (A1 mini), cube centred on each of 2 plates
        small = 180
        plate2_x = small * 1.2 + small / 2
        settings = bambu_settings(bed=small)
        report, out, _ = self.convert(settings, plates=[[(90, 90)], [(plate2_x, 90)]])
        self.assertEqual(report.moved_objects, 2)
        xs = [list(map(float, t.split()))[9:11] for t in
              re.findall(r'transform="([^"]+)"', read(out, "3D/3dmodel.model"))]
        self.assertAlmostEqual(xs[0][0], 128)
        self.assertAlmostEqual(xs[0][1], 128)
        self.assertAlmostEqual(xs[1][0], 256 * 1.2 + 128)
        self.assertAlmostEqual(xs[1][1], 128)

    def test_prime_tower_moves_with_the_bed(self):
        settings = bambu_settings(bed=180, wipe_tower_x=["90", "170"], wipe_tower_y=["45"],
                                  prime_tower_width="35")
        _, _, s = self.convert(settings)
        self.assertEqual(s["wipe_tower_x"], ["128", "221"])  # clamped to bed
        self.assertEqual(s["wipe_tower_y"], ["64"])

    def test_bed_type_comes_from_the_printer(self):
        _write(self.tmp / "profiles" / "Testvendor" / "machine" / "Test C5P.json",
               {"name": "Test C5P", "type": "machine_model", "default_bed_type": "Textured PEI Plate"})
        lib = ProfileLibrary(self.tmp / "profiles", "Testvendor")
        src = write_project(self.tmp / "bed.3mf", bambu_settings(curr_bed_type="Cool Plate"), [[(10, 10)]])
        convert(src, self.tmp / "bed-out.3mf", TARGET, lib)
        s = json.loads(read(self.tmp / "bed-out.3mf", "Metadata/project_settings.config"))
        self.assertEqual(s["curr_bed_type"], "Textured PEI Plate")

    def test_newer_bambu_files_are_relabelled_for_the_target_slicer(self):
        settings = bambu_settings(version="02.09.01.70",
                                  filament_colour=["#D6B685FF", "#141a0f", "#63860FFF", "bad"])
        _, out, s = self.convert(settings)
        self.assertEqual(s["version"], TARGET.file_version)
        model = read(out, "3D/3dmodel.model")
        self.assertIn(f"BambuStudio-{TARGET.file_version}<", model)
        self.assertIn(f'<metadata name="OrcaSlicer">{TARGET.orca_version}</metadata>', model)
        self.assertEqual(s["filament_colour"], ["#D6B685", "#141A0F", "#63860F", "#FFFFFF"])

    def test_bambu_per_nozzle_type_lists_are_cut_to_one_per_filament(self):
        # bambu 2.9: 4 filaments x 3 nozzle variants
        variants = ["Direct Drive Standard", "Direct Drive High Flow", "Direct Drive E3D High Flow"]
        settings = bambu_settings(
            filament_extruder_variant=variants * 4,
            filament_self_index=[str(i) for i in range(1, 5) for _ in range(3)],
            filament_flush_temp=[f"{slot}{v}" for slot in range(1, 5) for v in "SHE"],
            nozzle_flush_dataset=["1", "2", "3"],
            tree_support_wall_count="-1")
        _, _, s = self.convert(settings)
        self.assertEqual(s["filament_flush_temp"], ["1S", "2S", "3S", "4S"])
        self.assertEqual(s["filament_self_index"], ["1", "2", "3", "4"])
        self.assertNotIn("nozzle_flush_dataset", s)

    def test_bambu_value_names_are_translated(self):
        _, _, s = self.convert(bambu_settings(ensure_vertical_shell_thickness="enabled"))
        self.assertEqual(s["ensure_vertical_shell_thickness"], "ensure_all")
        _, _, s = self.convert(bambu_settings(ensure_vertical_shell_thickness="something_new"))
        self.assertNotIn("ensure_vertical_shell_thickness", s)

    def test_plate_grid_matches_slicer(self):
        self.assertEqual([_plate_columns(n) for n in (1, 2, 3, 4, 5, 9, 10)], [1, 2, 2, 2, 3, 3, 4])
        # plate 3 of 4 is on the second row
        self.assertEqual(_plate_centre(2, 2, (0, 0, 100, 100)), (50, -70))


@unittest.skipUnless(
    any((r / "Flashforge" / "machine").is_dir() for r in __import__("plateshift.profiles", fromlist=["x"]).default_profile_roots()),
    "Flash Studio / OrcaSlicer Flashforge profiles are not installed")
class InstalledProfilesTest(unittest.TestCase):
    # uses the real installed Flashforge presets

    def test_c5p_conversion(self):
        target = BY_KEY["c5p"]
        lib = ProfileLibrary(find_profile_root("Flashforge"), "Flashforge")
        tmp = Path(tempfile.mkdtemp())
        src = write_project(tmp / "in.3mf", bambu_settings(), [[(128, 128)]])
        report = convert(src, tmp / "out.3mf", target, lib)
        s = json.loads(read(tmp / "out.3mf", "Metadata/project_settings.config"))
        self.assertEqual(s["printer_settings_id"], "Flashforge Creator 5 Pro 0.4 nozzle")
        self.assertEqual(len(s["nozzle_diameter"]), 4)
        self.assertTrue(all(n.endswith("@FF C5P") for n in s["filament_settings_id"]), s["filament_settings_id"])
        self.assertIn("Silk", s["filament_settings_id"][1])
        self.assertEqual(s["filament_type"][2], "PETG")
        self.assertIn("C5", report.process)


if __name__ == "__main__":
    unittest.main()
