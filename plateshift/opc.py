"""Read OrcaSlicer's packed system profiles (<vendor>.opc, OrcaSlicer 2.4+).

Layout, as described in OrcaSlicer's docs/HLSD/preset-cache.md:
  20 byte header: magic "ZCRO", format version, body size, crc32 of the body
  body (cereal binary, little endian; strings and lists are a u64 count first):
    u32 format version, vendor name, vendor version
    dictionary: option keys, the type each was written as, enum value names
    vendor profiles (skipped here)
    process, filament and machine presets: name, sub path, own settings,
    inherits, includes, description, instantiation, setting id, filament id,
    renamed from
Values come back as the strings a json profile would hold, so the rest of
plateshift doesn't care which form a vendor came in.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

MAGIC = 0x4F52435A
FORMAT = 2
VECTOR = 0x4000
INT_NIL = 2**31 - 1


class OpcError(Exception):
    pass


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise OpcError("file ends early")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        return struct.unpack_from("<" + fmt, self.take(size))

    def u8(self):
        return self.unpack("B")[0]

    def u16(self):
        return self.unpack("H")[0]

    def u32(self):
        return self.unpack("I")[0]

    def u64(self):
        return self.unpack("Q")[0]

    def count(self, limit=10_000_000):
        n = self.u64()
        if n > limit:
            raise OpcError("implausible list length")
        return n

    def string(self) -> str:
        return self.take(self.count(50_000_000)).decode("utf-8", "replace")

    def strings(self) -> list[str]:
        return [self.string() for _ in range(self.count())]


def _num(v: float) -> str:
    if math.isnan(v):
        return "nil"
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return repr(v)


def _int(v: int) -> str:
    return "nil" if v == INT_NIL else str(v)


def _value(r: _Reader, kind: int, enums: list[str]):
    base, vector = kind & ~VECTOR, bool(kind & VECTOR)
    if base == 9:  # enum(s): u32 count, then u16 name index (+ raw i32 when unnamed)
        out = []
        for _ in range(r.u32()):
            idx = r.u16()
            if idx >= len(enums):
                raise OpcError("enum index out of range")
            out.append(_int(r.unpack("i")[0]) if idx == 0 else enums[idx])
        return out if vector else (out[0] if out else "")
    if kind == 10 | VECTOR:  # points groups
        return [",".join(f"{_num(x)}x{_num(y)}" for x, y in
                         (r.unpack("dd") for _ in range(r.count()))) for _ in range(r.count())]
    if kind == 11 | VECTOR:  # ints groups
        return [",".join(_int(r.unpack("i")[0]) for _ in range(r.count())) for _ in range(r.count())]
    if base == 6 and vector:  # points: raw size_t count then x,y doubles
        return [f"{_num(x)}x{_num(y)}" for x, y in (r.unpack("dd") for _ in range(r.count()))]

    def one():
        if base in (1, 4):
            v = _num(r.unpack("d")[0])
            return v + "%" if base == 4 and v != "nil" else v
        if base == 2:
            return _int(r.unpack("i")[0])
        if base == 3:
            return r.string()
        if base == 5:
            v, pct = r.unpack("d?")
            return _num(v) + ("%" if pct else "")
        if base == 6:  # a single point is "x,y"; lists of points use "x" (above)
            x, y = r.unpack("dd")
            return f"{_num(x)},{_num(y)}"
        if base == 7:
            return "x".join(_num(c) for c in r.unpack("ddd"))
        if base == 8:
            return "1" if r.u8() else "0"
        raise OpcError(f"unknown option type {kind:#x}")

    if vector:
        return [one() for _ in range(r.count())]
    return one()


def _skip_vendors(r: _Reader) -> None:
    for _ in range(r.count()):
        r.string()                       # map key
        for _ in range(5):               # name, id, version, update url, changelog url
            r.string()
        for _ in range(r.count()):       # printer models
            r.string(), r.string(), r.string()
            r.u8()                       # technology
            r.string()                   # family
            r.strings()                  # variants
            r.strings()                  # default materials
            r.strings()                  # unsupported bed types
            for _ in range(9):
                r.string()
        r.strings()                      # default filaments
        r.strings()                      # default sla materials


def read(path: Path) -> dict:
    """{'vendor', 'version', 'process': [...], 'filament': [...], 'machine': [...]}.
    Each preset is a dict shaped like a json profile, plus '_includes'."""
    data = Path(path).read_bytes()
    if len(data) < 20:
        raise OpcError("too short")
    magic, version, size, crc = struct.unpack_from("<IIQI", data)
    if magic != MAGIC:
        raise OpcError("not an OrcaSlicer profile cache")
    if version != FORMAT:
        raise OpcError(f"cache format {version}, this reader knows {FORMAT}")
    body = data[20:]
    if size != len(body) or zlib.crc32(body) != crc:
        raise OpcError("damaged (size or checksum wrong)")

    r = _Reader(body)
    if r.u32() != FORMAT:
        raise OpcError("cache format mismatch")
    vendor, vendor_version = r.string(), r.string()
    keys = r.strings()
    types = [r.u16() for _ in range(r.count())]
    enums = r.strings()
    if len(types) != len(keys):
        raise OpcError("dictionary is inconsistent")
    _skip_vendors(r)

    out = {"vendor": vendor, "version": vendor_version}
    for kind in ("process", "filament", "machine"):
        presets = []
        for _ in range(r.u32()):
            name, sub_path = r.string(), r.string()
            config = {}
            for _ in range(r.u32()):
                idx = r.u16()
                if idx >= len(keys):
                    raise OpcError("option index out of range")
                config[keys[idx]] = _value(r, types[idx], enums)
            inherits, includes = r.string(), r.strings()
            description, instantiation = r.string(), r.string()
            setting_id, filament_id = r.string(), r.string()
            renamed = r.strings()
            preset = dict(config)
            preset.update(name=name, type=kind, instantiation=instantiation or "true")
            for k, v in (("inherits", inherits), ("setting_id", setting_id),
                         ("filament_id", filament_id), ("description", description)):
                if v:
                    preset[k] = v
            if renamed:
                preset["renamed_from"] = renamed
            if includes:
                preset["_includes"] = includes
            preset["_source"] = f"{Path(path).name}:{sub_path}"
            presets.append(preset)
        out[kind] = presets
    r.u64()  # parse error count
    if r.pos != len(body):
        raise OpcError("unexpected data after the presets")
    return out
