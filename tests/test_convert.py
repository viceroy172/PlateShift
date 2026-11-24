from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_project import bambu_settings, write_project  # noqa: E402
from plateshift import paint  # noqa: E402
from plateshift.convert import (ConversionError, _clear_prime_towers, _depth_for_height,  # noqa: E402
                                _drop_gcode_rels, _filament_lists, _overlaps, _plate_centre, _plate_columns,
                                _tower_size, analyse, choose_filament, process_choices,
                                convert, unique_output)
from plateshift.printers import BY_KEY, Target  # noqa: E402
from plateshift.profiles import ProfileLibrary, locate  # noqa: E402

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


def _replace_entry(path: Path, name: str, data: bytes) -> None:
    with zipfile.ZipFile(path) as z:
        items = [(i, z.read(i.filename)) for i in z.infolist()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for info, content in items:
            z.writestr(info, data if info.filename == name else content)


def read(path: Path, name: str) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read(name).decode("utf-8")


class ConvertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
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
        convert(src, self.tmp / "u-out.3mf", TARGET, lib, overrides={2: "My SBS"})
        s = json.loads(read(self.tmp / "u-out.3mf", "Metadata/project_settings.config"))
        self.assertEqual(s["filament_settings_id"][1], "My SBS")
        self.assertEqual(s["nozzle_temperature"][1], "235")
        self.assertEqual(s["inherits_group"][2], "Generic PLA @T")
        # auto choice never picks user presets
        self.assertNotIn("My SBS", s["filament_settings_id"][0])

    def test_user_folders_of_every_slicer_are_found(self):
        from unittest import mock
        from plateshift import profiles
        base = self.tmp / "config"
        for folder in ("Orca-Flashforge/user/default/filament", "OrcaSlicer/user/default/process",
                       "Creality/Creality Print/7.0/user/123/process", "Creality/Creality Print/6.0/user/123/process",
                       "Creality/Creality Print/7.0/user/Temp", "OrcaSlicer/user/default/machine"):
            (base / folder).mkdir(parents=True, exist_ok=True)
        with mock.patch.object(profiles, "_config_dirs", lambda: [base]):
            found = [(label, d.relative_to(base).as_posix()) for label, d in profiles.user_preset_dirs()]
        self.assertEqual(found, [("Flash Studio", "Orca-Flashforge/user/default"),
                                 ("OrcaSlicer", "OrcaSlicer/user/default"),
                                 ("Creality Print", "Creality/Creality Print/7.0/user/123"),
                                 ("Creality Print", "Creality/Creality Print/6.0/user/123")])

    def test_your_printer_presets_are_never_read(self):
        user = self.tmp / "user"
        _write(user / "machine" / "My printer.json", {"name": "My printer", "inherits": MACHINE,
                                                      "print_host": "192.168.1.9", "printhost_apikey": "secret"})
        lib = ProfileLibrary(self.tmp / "profiles", "Testvendor", [("OrcaSlicer", user)])
        with self.assertRaises(KeyError):
            lib.resolve("machine", "My printer")

    def my_process_library(self):
        user = self.tmp / "user"
        _write(user / "process" / "0.20mm Mine @T.json", {
            "name": "0.20mm Mine @T", "inherits": "0.20mm Standard @T", "from": "User",
            "wall_loops": "4", "sparse_infill_density": "40%"})
        return ProfileLibrary(self.tmp / "profiles", "Testvendor", [("Flash Studio", user)])

    def test_your_print_profile_wins_over_the_designer(self):
        lib = self.my_process_library()
        mine = [p for p in process_choices(lib, TARGET) if p.get("_user")]
        self.assertEqual([(p["name"], p["_user"]) for p in mine], [("0.20mm Mine @T", "Flash Studio")])
        self.assertEqual(process_choices(lib, TARGET)[0]["name"], "0.20mm Mine @T")  # yours first
        src = write_project(self.tmp / "p.3mf", bambu_settings(wall_filament="2"), [[(128, 128)]])
        report = convert(src, self.tmp / "p-out.3mf", TARGET, lib, process="0.20mm Mine @T")
        s = json.loads(read(self.tmp / "p-out.3mf", "Metadata/project_settings.config"))
        self.assertEqual(s["print_settings_id"], "0.20mm Mine @T")
        # the project asked for 0.16 mm, 3 walls, 25%, supports; your profile says otherwise
        self.assertEqual((s["layer_height"], s["wall_loops"], s["sparse_infill_density"], s["enable_support"]),
                         ("0.20", "4", "40%", "0"))
        # which filament prints what still follows the project's colours
        self.assertEqual(s["wall_filament"], "2")
        self.assertIn("yours, from Flash Studio", report.process)

    def test_designer_settings_can_go_over_your_print_profile(self):
        lib = self.my_process_library()
        src = write_project(self.tmp / "p.3mf", bambu_settings(), [[(128, 128)]])
        convert(src, self.tmp / "p-out.3mf", TARGET, lib, process="0.20mm Mine @T", keep_design=True)
        s = json.loads(read(self.tmp / "p-out.3mf", "Metadata/project_settings.config"))
        self.assertEqual(s["print_settings_id"], "0.20mm Mine @T")
        self.assertEqual((s["layer_height"], s["wall_loops"], s["sparse_infill_density"]), ("0.16", "3", "25%"))

    def test_automatic_print_profile_never_picks_yours(self):
        lib = self.my_process_library()
        src = write_project(self.tmp / "p.3mf", bambu_settings(layer_height="0.20"), [[(128, 128)]])
        convert(src, self.tmp / "p-out.3mf", TARGET, lib)
        s = json.loads(read(self.tmp / "p-out.3mf", "Metadata/project_settings.config"))
        self.assertEqual(s["print_settings_id"], "0.20mm Standard @T")

    def test_unknown_print_profile_is_a_plain_error(self):
        src = write_project(self.tmp / "p.3mf", bambu_settings(), [[(128, 128)]])
        with self.assertRaisesRegex(ConversionError, "print profile 'Nope'"):
            convert(src, self.tmp / "p-out.3mf", TARGET, self.lib, process="Nope")

    def test_user_presets_follow_renamed_parents(self):
        # orca renamed "Creality Generic PLA" to "Generic PLA @Creality"; old user presets still work
        _write(self.tmp / "profiles" / "Testvendor" / "filament" / "Generic PLA @T.json", {
            "name": "Generic PLA @T", "inherits": "Generic PLA @System", "instantiation": "true",
            "compatible_printers": [MACHINE], "renamed_from": ["Old Generic PLA"]})
        user = self.tmp / "user2"
        _write(user / "filament" / "Old SBS.json", {"name": "Old SBS", "inherits": "Old Generic PLA",
                                                     "nozzle_temperature": ["232"]})
        lib = ProfileLibrary(self.tmp / "profiles", "Testvendor", [("OrcaSlicer", user)])
        p = lib.resolve("filament", "Old SBS")
        self.assertEqual(p["_parent"], "Generic PLA @T")
        self.assertIn(MACHINE, p["compatible_printers"])

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
        # 170 scales to 241.8; kept on the bed with room for the tower, its brim and a gap
        side, _, brim = _tower_size(s, len(s["filament_colour"]))
        self.assertEqual(s["wipe_tower_x"][0], "128")
        self.assertAlmostEqual(float(s["wipe_tower_x"][1]), 256 - side - brim - 5, places=2)
        self.assertEqual(s["wipe_tower_y"], ["64", "64"])  # one per plate, like x

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

    def test_older_bambu_files_are_relabelled_too(self):
        # found by hand: flash studio 2.4 called a bambu studio 2.5.0.66 file newer than itself
        _, out, s = self.convert(bambu_settings(version="02.05.00.66"))
        self.assertEqual(s["version"], TARGET.file_version)
        model = read(out, "3D/3dmodel.model")
        self.assertIn(f"BambuStudio-{TARGET.file_version}<", model)
        self.assertNotIn("02.05.00.66", model)
        self.assertIn(f'<metadata name="OrcaSlicer">{TARGET.orca_version}</metadata>', model)

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

    # --- found in review ---------------------------------------------------

    def test_new_toolhead_layout_picks_presets_for_its_colours(self):
        # swapping PLA colour 1 and PETG colour 3 used to keep the old presets:
        # PETG on a PLA preset and the other way round
        src = write_project(self.tmp / "in.3mf", bambu_settings(), [[(128, 128)]])
        plan = analyse(src, TARGET, self.lib)
        before = dict(plan.presets)
        report = convert(src, self.tmp / "out.3mf", TARGET, self.lib, merge=[3, 2, 1, 4], plan=plan)
        by_head = {s.slot: (s.source_type, s.target) for s in report.slots}
        self.assertEqual(by_head[1], ("PETG", "Generic PETG @T"))
        self.assertEqual(by_head[3][0], "PLA")
        self.assertIn("PLA", by_head[3][1])
        self.assertEqual(plan.presets, before)  # the caller's plan is left alone

    def test_relationships_keep_their_root_element(self):
        rels = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
                ' <Relationship Target="/Metadata/plate_1.gcode" Id="rel-1" Type="g"/>\n'
                ' <Relationship Target="/Metadata/pic.png" Id="rel-2" Type="p"/>\n'
                '</Relationships>\n')
        out = _drop_gcode_rels(rels)
        root = ET.fromstring(out)
        self.assertTrue(root.tag.endswith("Relationships"))
        self.assertEqual([r.get("Id") for r in root], ["rel-2"])
        self.assertIsNone(_drop_gcode_rels(rels.replace("pic.png", "plate_2.gcode")))

    def test_damaged_files_give_a_plain_error(self):
        cases = {
            "settings are a list": None,
            "unreadable bed": bambu_settings(printable_area=["0x0", "abc"]),
        }
        for label, settings in cases.items():
            with self.subTest(label):
                src = write_project(self.tmp / "bad.3mf", settings or {}, [[(128, 128)]])
                if settings is None:
                    _replace_entry(src, "Metadata/project_settings.config", b"[1, 2, 3]")
                with self.assertRaises(ConversionError):
                    convert(src, self.tmp / "bad-out.3mf", TARGET, self.lib)

    def test_damaged_parts_inside_a_project_give_a_plain_error(self):
        src = write_project(self.tmp / "m.3mf", bambu_settings(), [[(128, 128)]])
        for entry, data in [("Metadata/model_settings.config", b"<config><plate>"),
                            ("3D/3dmodel.model", b"\xff\xfe not utf-8 \x80"),
                            ("3D/3dmodel.model", b'<model><triangle paint_color="' + b"F" * 3000 + b'"/></model>')]:
            with self.subTest(entry):
                broken = self.tmp / "broken.3mf"
                broken.write_bytes(src.read_bytes())
                _replace_entry(broken, entry, data)
                with self.assertRaises(ConversionError):
                    convert(broken, self.tmp / "broken-out.3mf", TARGET, self.lib)

    def test_odd_but_harmless_values_are_tolerated(self):
        # a text filament list, a "nan" support filament, a non-numeric plate id
        settings = bambu_settings(filament_settings_id="Bambu PLA Basic @BBL X1C", filament_type="PLA",
                                  filament_colour="#000000", support_filament="nan")
        src = write_project(self.tmp / "odd.3mf", settings, [[(128, 128)]])
        ms = read(src, "Metadata/model_settings.config").replace('value="1"/>', 'value="one"/>', 1)
        _replace_entry(src, "Metadata/model_settings.config", ms.encode())
        report = convert(src, self.tmp / "odd-out.3mf", TARGET, self.lib)
        self.assertEqual(len(report.slots), 1)

    def test_lists_that_only_look_per_filament_are_not_shuffled(self):
        # a 4-point bed exclusion area in a 4-filament project isn't per filament
        area = ["0x0", "18x0", "18x28", "0x28"]
        _, _, s = self.convert(bambu_settings(bed_exclude_area=area), merge=[1, 1, 2, 2])
        self.assertNotEqual(s.get("bed_exclude_area"), ["0x0", "18x28"])
        self.assertIn(s.get("bed_exclude_area"), (None, area))

    def test_compatibility_follows_orcas_rules(self):
        fil = self.tmp / "profiles" / "Testvendor" / "filament"
        # 1. names no printers: suits every printer of its brand
        _write(fil / "Testvendor PLA Silk.json", {"name": "Testvendor PLA Silk", "inherits": "Generic PLA @System",
                                                   "instantiation": "true"})
        # 2. a condition decides
        _write(fil / "Not for T.json", {"name": "Not for T", "inherits": "Generic PLA @System",
                                        "instantiation": "true",
                                        "compatible_printers_condition": 'printer_model != "Test C5P"'})
        _write(fil / "Only for T.json", {"name": "Only for T", "inherits": "Generic PLA @System",
                                         "instantiation": "true",
                                         "compatible_printers_condition": 'printer_model == "Test C5P"'})
        _write(fil / "Garbled.json", {"name": "Garbled", "inherits": "Generic PLA @System", "instantiation": "true",
                                      "compatible_printers_condition": 'printer_model === "Test C5P"'})
        # the shared library's presets with no list are bases, not offered
        _write(self.tmp / "profiles" / "OrcaFilamentLibrary" / "filament" / "Library PLA.json",
               {"name": "Library PLA", "inherits": "Generic PLA @System", "instantiation": "true"})
        lib = ProfileLibrary(self.tmp / "profiles", "Testvendor")
        found = {p["name"]: p for p in lib.compatible("filament", MACHINE)}
        self.assertIn("Testvendor PLA Silk", found)
        self.assertIn("Only for T", found)
        self.assertNotIn("Not for T", found)
        self.assertNotIn("Garbled", found)          # unreadable condition: hidden, not guessed
        self.assertNotIn("Library PLA", found)
        self.assertNotIn("Other printer PLA", found)  # lists a different printer
        self.assertTrue(found["Generic PLA @T"].get("_explicit"))
        self.assertFalse(found["Testvendor PLA Silk"].get("_explicit"))

    def test_presets_tuned_for_the_printer_beat_brand_wide_ones(self):
        # "Testvendor PLA Basic" (no printer list) vs "Testvendor PLA Basic @T" (lists the printer):
        # same name words, the shorter name used to win the tie
        _write(self.tmp / "profiles" / "Testvendor" / "filament" / "Testvendor PLA Basic.json",
               {"name": "Testvendor PLA Basic", "inherits": "Generic PLA @System", "instantiation": "true"})
        lib = ProfileLibrary(self.tmp / "profiles", "Testvendor")
        src = write_project(self.tmp / "t.3mf", bambu_settings(), [[(128, 128)]])
        convert(src, self.tmp / "t-out.3mf", TARGET, lib)
        s = json.loads(read(self.tmp / "t-out.3mf", "Metadata/project_settings.config"))
        self.assertEqual(s["filament_settings_id"][0], "Testvendor PLA Basic @T")

    def test_mixed_temperatures_on_one_nozzle_are_flagged(self):
        # PLA 210 and PETG 240 can't share one nozzle; slicers refuse to slice it
        for name, extra in (("Generic PLA @System", {"nozzle_temperature_range_low": ["190"],
                                                      "nozzle_temperature_range_high": ["230"]}),
                            ("Generic PETG @System", {"nozzle_temperature_range_low": ["220"],
                                                       "nozzle_temperature_range_high": ["260"]})):
            path = self.tmp / "profiles" / "OrcaFilamentLibrary" / "filament" / f"{name}.json"
            data = json.loads(path.read_text())
            data.update(extra)
            path.write_text(json.dumps(data))
        one = self.tmp / "profiles" / "Testvendor" / "machine" / f"{MACHINE}.json"
        machine = json.loads(one.read_text())
        machine["nozzle_diameter"] = ["0.4"]
        one.write_text(json.dumps(machine))
        lib = ProfileLibrary(self.tmp / "profiles", "Testvendor")
        src = write_project(self.tmp / "mix.3mf", bambu_settings(), [[(128, 128)]])  # PLA, PLA, PETG, PLA
        report = convert(src, self.tmp / "mix-out.3mf", TARGET, lib)
        self.assertTrue(any("PLA (210°C) and PETG (240°C)" in w for w in report.warnings), report.warnings)
        # all PLA: nothing to warn about
        pla = bambu_settings(filament_type=["PLA"] * 4, filament_settings_id=["Bambu PLA Basic @BBL X1C"] * 4)
        report = convert(write_project(self.tmp / "pla.3mf", pla, [[(128, 128)]]), self.tmp / "pla-o.3mf", TARGET, lib)
        self.assertFalse(any("nozzle temperatures" in w for w in report.warnings))

    def test_slot_word_follows_the_printer(self):
        ifs = Target("i", "IFS printer", "Testvendor", MACHINE, "0.20mm Standard @T", 4, "I", slot="Slot")
        report = convert(write_project(self.tmp / "s.3mf", bambu_settings(), [[(128, 128)]]),
                         self.tmp / "s-out.3mf", ifs, self.lib)
        self.assertTrue(report.lines()[3].startswith("  Slot 1 "))
        self.assertEqual(TARGET.slot_word, "Toolhead")
        self.assertEqual(Target("o", "One", "V", "M", "P", 1, "O").slot_word, "Filament")

    def test_plate_type_suits_every_filament(self):
        # orca refuses to slice PETG on a Cool Plate (its cool plate temperature is 0)
        petg = self.tmp / "profiles" / "OrcaFilamentLibrary" / "filament" / "Generic PETG @System.json"
        data = json.loads(petg.read_text())
        data.update(cool_plate_temp=["0"], textured_plate_temp=["70"])
        petg.write_text(json.dumps(data))
        lib = ProfileLibrary(self.tmp / "profiles", "Testvendor")
        src = write_project(self.tmp / "plate.3mf", bambu_settings(curr_bed_type="Cool Plate"), [[(128, 128)]])
        convert(src, self.tmp / "plate-out.3mf", TARGET, lib)
        s = json.loads(read(self.tmp / "plate-out.3mf", "Metadata/project_settings.config"))
        self.assertEqual(s["curr_bed_type"], "Textured PEI Plate")
        # all PLA: the project's Cool Plate is fine and stays
        settings = bambu_settings(curr_bed_type="Cool Plate", filament_type=["PLA"] * 4,
                                  filament_settings_id=["Bambu PLA Basic @BBL X1C"] * 4)
        src = write_project(self.tmp / "pla.3mf", settings, [[(128, 128)]])
        convert(src, self.tmp / "pla-out.3mf", TARGET, lib)
        s = json.loads(read(self.tmp / "pla-out.3mf", "Metadata/project_settings.config"))
        self.assertEqual(s["curr_bed_type"], "Cool Plate")

    def test_unique_output_never_reuses_a_name(self):
        first = self.tmp / "x - T.3mf"
        self.assertEqual(unique_output(first), first)
        first.write_text("taken")
        second = unique_output(first)
        self.assertEqual(second.name, "x - T (2).3mf")
        second.write_text("taken")
        self.assertEqual(unique_output(first).name, "x - T (3).3mf")

    def test_report_text(self):
        report = convert(self.six_colour_project(), self.tmp / "r.3mf", TARGET, self.lib)
        text = "\n".join(report.lines())
        self.assertIn("six.3mf -> r.3mf", text)
        self.assertIn("Toolhead 1 #000000: colours 1 + 2 merged", text)
        self.assertIn("Repainted", text)

    def test_plate_grid_matches_slicer(self):
        self.assertEqual([_plate_columns(n) for n in (1, 2, 3, 4, 5, 9, 10)], [1, 2, 2, 2, 3, 3, 4])
        # plate 3 of 4 is on the second row
        self.assertEqual(_plate_centre(2, 2, (0, 0, 100, 100)), (50, -70))


class FilamentListTests(unittest.TestCase):
    def test_a_setting_one_preset_lacks_is_never_left_empty(self):
        # found by the k2 plus e2e run: "1,,1,1" made orca refuse the project
        out = {}
        chosen = [{"name": "a", "activate_chamber_temp_control": ["1"]}, {"name": "b"},
                  {"name": "c", "activate_chamber_temp_control": ["1"]}]
        _filament_lists(out, {}, chosen, [0, 1, 2])
        self.assertEqual(out["activate_chamber_temp_control"], ["1", "1", "1"])

    def test_the_project_value_comes_first(self):
        out = {}
        chosen = [{"name": "a", "chamber_temperature": ["40"]}, {"name": "b"}]
        _filament_lists(out, {"chamber_temperature": ["0", "35"]}, chosen, [0, 1])
        self.assertEqual(out["chamber_temperature"], ["40", "35"])


def _square(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


class PrimeTowerTests(unittest.TestCase):
    TOWER = {"prime_tower_width": "35", "prime_volume": "45", "layer_height": "0.2",
             "prime_tower_brim_width": "3", "enable_prime_tower": "1"}

    def test_tower_is_square_whatever_its_width(self):
        narrow = _tower_size(dict(self.TOWER), 4)
        wide = _tower_size(dict(self.TOWER, prime_tower_width="180"), 4)
        self.assertEqual(narrow[0], narrow[1])
        self.assertAlmostEqual(narrow[0], wide[0])

    def test_flushing_into_the_tower_makes_it_bigger(self):
        flush = dict(self.TOWER, flush_volumes_matrix=["0", "280", "280", "0"], flush_multiplier=["1"])
        kept_out = _tower_size(dict(flush, purge_in_prime_tower="0"), 4)[0]
        purged = _tower_size(dict(flush, purge_in_prime_tower="1"), 4)[0]
        self.assertGreater(purged, kept_out * 2)
        # orca made this tower about 65 mm square for the CFS profiles
        self.assertTrue(66 < purged < 85, purged)

    def test_tall_towers_are_deeper(self):
        self.assertEqual(_depth_for_height(0), 0)
        self.assertAlmostEqual(_depth_for_height(175), 30)
        self.assertEqual(_depth_for_height(500), 60)

    def test_tower_on_an_object_moves_to_a_clear_spot(self):
        settings = dict(self.TOWER, wipe_tower_x=["100"], wipe_tower_y=["100"])
        model = (0, _square(80, 80, 180, 180), 50.0)
        self.assertEqual(_clear_prime_towers(settings, (0, 0, 256, 256), [model], 4), [])
        x, y = float(settings["wipe_tower_x"][0]), float(settings["wipe_tower_y"][0])
        side, _, brim = _tower_size(settings, 4, 50.0)
        gap = brim + 5
        clear = x + side + gap <= 80 or x - gap >= 180 or y + side + gap <= 80 or y - gap >= 180
        self.assertTrue(clear, (x, y))

    def test_a_clear_tower_stays_put(self):
        settings = dict(self.TOWER, wipe_tower_x=["20"], wipe_tower_y=["20"])
        _clear_prime_towers(settings, (0, 0, 256, 256), [(0, _square(150, 150, 200, 200), 50.0)], 4)
        self.assertEqual((settings["wipe_tower_x"], settings["wipe_tower_y"]), (["20"], ["20"]))

    def test_no_room_is_reported_per_plate(self):
        settings = dict(self.TOWER, wipe_tower_x=["20", "20"], wipe_tower_y=["20"])
        everything = (0, _square(10, 10, 246, 246), 50.0)
        self.assertEqual(_clear_prime_towers(settings, (0, 0, 256, 256), [everything], 4), [0])

    def test_a_tower_in_the_empty_corner_of_a_round_object_stays(self):
        # a diamond fills only half its bounding box; the tower sits in a corner it leaves free
        diamond = [(150, 50), (250, 150), (150, 250), (50, 150)]
        settings = dict(self.TOWER, wipe_tower_x=["200"], wipe_tower_y=["20"])
        _clear_prime_towers(settings, (0, 0, 256, 256), [(0, diamond, 50.0)], 4)
        self.assertEqual((settings["wipe_tower_x"], settings["wipe_tower_y"]), (["200"], ["20"]))
        self.assertTrue(_overlaps((140, 140, 160, 160), diamond))
        self.assertFalse(_overlaps((200, 0, 256, 60), diamond))

    def test_single_colour_has_no_tower_to_move(self):
        settings = dict(self.TOWER, wipe_tower_x=["100"], wipe_tower_y=["100"])
        self.assertEqual(_clear_prime_towers(settings, (0, 0, 256, 256), [(0, _square(0, 0, 256, 256), 9.0)], 1),
                         [])
        self.assertEqual(settings["wipe_tower_x"], ["100"])


@unittest.skipUnless(
    any((r / "Flashforge" / "machine").is_dir() for r in __import__("plateshift.profiles", fromlist=["x"]).default_profile_roots()),
    "Flash Studio / OrcaSlicer Flashforge profiles are not installed")
class InstalledProfilesTest(unittest.TestCase):
    # uses the real installed Flashforge presets

    def test_c5p_conversion(self):
        target = BY_KEY["c5p"]
        target, root = locate(target)
        lib = ProfileLibrary(root, "Flashforge")
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        src = write_project(tmp / "in.3mf", bambu_settings(), [[(128, 128)]])
        report = convert(src, tmp / "out.3mf", target, lib)
        s = json.loads(read(tmp / "out.3mf", "Metadata/project_settings.config"))
        self.assertEqual(s["printer_settings_id"], "Flashforge Creator 5 Pro 0.4 nozzle")
        self.assertEqual(len(s["nozzle_diameter"]), 4)
        self.assertTrue(all(n.endswith("@FF C5P") for n in s["filament_settings_id"]), s["filament_settings_id"])
        self.assertIn("Silk", s["filament_settings_id"][1])
        self.assertEqual(s["filament_type"][2], "PETG")
        self.assertIn("C5", report.process)


class ChooseFilamentTests(unittest.TestCase):
    PRESETS = [{"name": f"Flashforge {n} @FF AD5X", "filament_type": ["PLA"], "_explicit": True}
               for n in ("PLA Basic", "PLA LW", "PLA Silk", "PLA Matte")]

    def pick(self, source):
        return choose_filament(self.PRESETS, source, "PLA")[0]["name"]

    def test_plain_pla_gets_basic_not_a_shorter_variant(self):
        # "PLA LW" (foaming lightweight PLA) used to win the tie on name length
        self.assertEqual(self.pick("Generic PLA @BBL X1C"), "Flashforge PLA Basic @FF AD5X")
        self.assertEqual(self.pick("Bambu PLA Basic @BBL X1C"), "Flashforge PLA Basic @FF AD5X")

    def test_named_variants_still_win(self):
        self.assertEqual(self.pick("Bambu PLA Silk @BBL X1C"), "Flashforge PLA Silk @FF AD5X")
        self.assertEqual(self.pick("Bambu PLA Matte @BBL X1C"), "Flashforge PLA Matte @FF AD5X")


if __name__ == "__main__":
    unittest.main()


class DiameterCheckTests(unittest.TestCase):
    def test_a_different_filament_diameter_is_flagged(self):
        from types import SimpleNamespace

        from plateshift.convert import Report, _check_diameters
        src = {"filament_diameter": ["1.75", "1.75", "1.75"]}
        plan = SimpleNamespace(merge=[1, 2, 2])
        report = Report(Path("a.3mf"), Path("b.3mf"), "", "")
        _check_diameters(src, {"filament_diameter": ["1.75", "1.75"]}, plan, report)
        self.assertEqual(report.warnings, [])
        _check_diameters(src, {"filament_diameter": ["1.75", "2.85"]}, plan, report)
        self.assertEqual(report.warnings, [
            "toolhead 2's filament is set up for 2.85 mm filament, the project used 1.75 mm; "
            "check that filament preset before printing"])
        quiet = Report(Path("a.3mf"), Path("b.3mf"), "", "")
        _check_diameters({}, {"filament_diameter": ["2.85"]}, plan, quiet)   # nothing to compare with
        self.assertEqual(quiet.warnings, [])
