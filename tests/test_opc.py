from __future__ import annotations

import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plateshift import opc  # noqa: E402
from plateshift.profiles import ProfileLibrary  # noqa: E402


def _s(text: str) -> bytes:
    data = text.encode()
    return struct.pack("<Q", len(data)) + data


def _strings(items) -> bytes:
    return struct.pack("<Q", len(items)) + b"".join(_s(i) for i in items)


def make_opc(path: Path, vendor: str, presets: dict) -> Path:
    """a small cache file in orca's layout. presets: kind -> list of (name, inherits, config)
    with config values as (key, type, python value)"""
    keys, types, enums = [], [], [""]
    for entries in presets.values():
        for _, _, config in entries:
            for key, kind, value in config:
                if key not in keys:
                    keys.append(key)
                    types.append(kind)
                if kind & 0xFF == 9:
                    for v in (value if isinstance(value, list) else [value]):
                        if v not in enums:
                            enums.append(v)

    def value_bytes(kind, value):
        if kind == 1:
            return struct.pack("<d", value)
        if kind == 0x4001:
            return struct.pack("<Q", len(value)) + b"".join(struct.pack("<d", v) for v in value)
        if kind == 2:
            return struct.pack("<i", value)
        if kind == 3:
            return _s(value)
        if kind == 0x4003:
            return _strings(value)
        if kind == 4:
            return struct.pack("<d", value)
        if kind == 5:
            return struct.pack("<d?", *value)
        if kind == 6:
            return struct.pack("<dd", *value)
        if kind == 0x4006:
            return struct.pack("<Q", len(value)) + b"".join(struct.pack("<dd", *p) for p in value)
        if kind == 8:
            return struct.pack("<?", value)
        if kind == 0x4008:
            return struct.pack("<Q", len(value)) + bytes(int(v) for v in value)
        if kind == 9:
            return struct.pack("<IH", 1, enums.index(value))
        if kind == 0x4009:
            return struct.pack("<I", len(value)) + b"".join(struct.pack("<H", enums.index(v)) for v in value)
        raise ValueError(kind)

    body = struct.pack("<I", 2) + _s(vendor) + _s("1.2.3")
    body += _strings(keys) + struct.pack("<Q", len(types)) + b"".join(struct.pack("<H", t) for t in types)
    body += _strings(enums)
    # one vendor profile with one printer model
    body += struct.pack("<Q", 1) + _s(vendor) + _s(vendor) + _s(vendor) + _s("1.2.3") + _s("") + _s("")
    body += struct.pack("<Q", 1) + _s("model") + _s("Model") + _s("") + b"\x00" + _s("fam")
    body += _strings(["0.4"]) + _strings([]) + _strings([]) + b"".join(_s("") for _ in range(9))
    body += _strings([]) + _strings([])
    for kind in ("process", "filament", "machine"):
        entries = presets.get(kind, [])
        body += struct.pack("<I", len(entries))
        for name, inherits, config in entries:
            body += _s(name) + _s(f"{kind}/{name}.json")
            body += struct.pack("<I", len(config))
            for key, k, value in config:
                body += struct.pack("<H", keys.index(key)) + value_bytes(k, value)
            body += _s(inherits) + _strings([]) + _s("") + _s("true") + _s("") + _s("") + _strings([])
    body += struct.pack("<Q", 0)
    path.write_bytes(struct.pack("<IIQI", opc.MAGIC, 2, len(body), zlib.crc32(body)) + body)
    return path


class OpcTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_values_come_back_like_json_profiles(self):
        path = make_opc(self.tmp / "Acme.opc", "Acme", {"machine": [("Acme X 0.4 nozzle", "", [
            ("layer_height", 1, 0.2), ("nozzle_diameter", 0x4001, [0.4, 0.4]),
            ("retract_lift_below", 2, 299), ("printer_model", 3, "Acme X"),
            ("compatible_printers", 0x4003, ["a", "b"]), ("infill_wall_overlap", 4, 15.0),
            ("initial_layer_line_width", 5, (120.0, True)), ("bed_mesh_max", 6, (99999.0, 99999.0)),
            ("printable_area", 0x4006, [(0, 0), (256, 0), (256, 256)]), ("auxiliary_fan", 8, True),
            ("wipe", 0x4008, [True, False]), ("gcode_flavor", 9, "klipper"),
            ("z_hop_types", 0x4009, ["Auto Lift", "Spiral Lift"]),
        ])]})
        p = opc.read(path)["machine"][0]
        self.assertEqual(p["layer_height"], "0.2")
        self.assertEqual(p["nozzle_diameter"], ["0.4", "0.4"])
        self.assertEqual(p["retract_lift_below"], "299")
        self.assertEqual(p["printer_model"], "Acme X")
        self.assertEqual(p["compatible_printers"], ["a", "b"])
        self.assertEqual(p["infill_wall_overlap"], "15%")
        self.assertEqual(p["initial_layer_line_width"], "120%")
        self.assertEqual(p["bed_mesh_max"], "99999,99999")
        self.assertEqual(p["printable_area"], ["0x0", "256x0", "256x256"])
        self.assertEqual(p["auxiliary_fan"], "1")
        self.assertEqual(p["wipe"], ["1", "0"])
        self.assertEqual(p["gcode_flavor"], "klipper")
        self.assertEqual(p["z_hop_types"], ["Auto Lift", "Spiral Lift"])

    def test_damaged_files_are_refused(self):
        path = make_opc(self.tmp / "Acme.opc", "Acme", {"machine": [("M", "", [("layer_height", 1, 0.2)])]})
        data = bytearray(path.read_bytes())
        data[-3] ^= 0xFF
        path.write_bytes(bytes(data))
        with self.assertRaisesRegex(opc.OpcError, "damaged"):
            opc.read(path)

    def test_library_reads_packed_vendors_and_inherits_across_them(self):
        make_opc(self.tmp / "OrcaFilamentLibrary.opc", "OrcaFilamentLibrary", {"filament": [
            ("Generic PLA @System", "", [("filament_type", 0x4003, ["PLA"]),
                                         ("nozzle_temperature", 0x4003, ["210"])])]})
        make_opc(self.tmp / "Acme.opc", "Acme", {
            "machine": [("Acme X 0.4 nozzle", "", [("printable_area", 0x4006, [(0, 0), (200, 200)])])],
            "filament": [("Acme PLA", "Generic PLA @System", [("compatible_printers", 0x4003, ["Acme X 0.4 nozzle"])])],
        })
        lib = ProfileLibrary(self.tmp, "Acme")
        names = [p["name"] for p in lib.compatible("filament", "Acme X 0.4 nozzle")]
        self.assertEqual(names, ["Acme PLA"])
        self.assertEqual(lib.resolve("filament", "Acme PLA")["filament_type"], ["PLA"])
        self.assertEqual(lib.resolve("machine", "Acme X 0.4 nozzle")["printable_area"], ["0x0", "200x200"])


if __name__ == "__main__":
    unittest.main()
