# SPDX-License-Identifier: AGPL-3.0-or-later
"""Preview pictures for the printer's screen, written into sliced g-code.

slicers run from the command line have no screen to draw on, so they leave the
thumbnails out although the printer profile asks for them. we scale the plate
picture saved in the project instead and add it the way orca does."""

from __future__ import annotations

import base64
import os
import re
import struct
import zlib
from pathlib import Path

from .preview import plate_pictures

LINE = 78   # base64 characters per line, as orca writes them
MAX_SIDE = 1024
_SIZE = re.compile(r"(\d+)\s*x\s*(\d+)(?:\s*/\s*(\w+))?", re.IGNORECASE)
_PLATE = re.compile(r"plate_(\d+)\.gcode$", re.IGNORECASE)
_HEAD = b"; HEADER_BLOCK_END"


# --- png ---------------------------------------------------------------

def _chunks(data: bytes):
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a png")
    pos = 8
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        yield kind, data[pos + 8:pos + 8 + length]
        pos += 12 + length


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def decode(data: bytes) -> tuple[int, int, bytearray]:
    """(width, height, rgba bytes) of a non-interlaced png of 8 or 16 bits"""
    header, idat, palette, trns = b"", [], b"", b""
    for kind, body in _chunks(data):
        if kind == b"IHDR":
            header = body
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"PLTE":
            palette = body
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IEND":
            break
    width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", header[:13])
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(colour)
    if channels is None or interlace or depth not in (8, 16) or (colour == 3 and depth != 8):
        raise ValueError("png kind not handled")
    if not (0 < width <= 8192 and 0 < height <= 8192):
        raise ValueError("png too big")
    step = channels * depth // 8
    stride = width * step
    raw = zlib.decompress(b"".join(idat))
    rows = bytearray(stride * height)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        kind = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if kind == 1:
            for i in range(step, stride):
                line[i] = (line[i] + line[i - step]) & 255
        elif kind == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif kind == 3:
            for i in range(stride):
                left = line[i - step] if i >= step else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif kind == 4:
            for i in range(stride):
                if i >= step:
                    line[i] = (line[i] + _paeth(line[i - step], prev[i], prev[i - step])) & 255
                else:
                    line[i] = (line[i] + prev[i]) & 255
        rows[y * stride:(y + 1) * stride] = line
        prev = line
    if depth == 16:   # keep the high byte of each sample
        rows = rows[0::2]
    count = width * height
    if colour == 6:
        return width, height, rows
    out = bytearray(count * 4)
    if colour == 2:
        out[0::4], out[1::4], out[2::4] = rows[0::3], rows[1::3], rows[2::3]
        out[3::4] = b"\xff" * count
    elif colour == 0:
        out[0::4] = out[1::4] = out[2::4] = rows
        out[3::4] = b"\xff" * count
    elif colour == 4:
        out[0::4] = out[1::4] = out[2::4] = rows[0::2]
        out[3::4] = rows[1::2]
    else:   # palette
        alpha = trns + b"\xff" * (256 - len(trns))
        table = [palette[i * 3:i * 3 + 3] + alpha[i:i + 1] for i in range(len(palette) // 3)]
        for i, index in enumerate(rows):
            out[i * 4:i * 4 + 4] = table[index] if index < len(table) else b"\0\0\0\0"
    return width, height, out


def encode(width: int, height: int, rgba: bytes) -> bytes:
    stride = width * 4
    raw = b"".join(b"\0" + bytes(rgba[y * stride:(y + 1) * stride]) for y in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


# --- scaling -----------------------------------------------------------

def _crop(width: int, height: int, rgba: bytearray) -> tuple[int, int, int, int]:
    """the box around everything that isn't see-through, a little margin kept"""
    alpha = rgba[3::4]
    rows = [y for y in range(height) if alpha[y * width:(y + 1) * width].strip(b"\0")]
    if not rows:
        return 0, 0, width, height
    left, right = width, 0
    for y in range(rows[0], rows[-1] + 1):
        line = alpha[y * width:(y + 1) * width]
        start = len(line) - len(line.lstrip(b"\0"))
        if start < width:
            left = min(left, start)
            right = max(right, len(line.rstrip(b"\0")))
    top, bottom = rows[0], rows[-1] + 1
    pad = max(right - left, bottom - top) // 20
    return max(0, left - pad), max(0, top - pad), min(width, right + pad), min(height, bottom + pad)


def fit(width: int, height: int, rgba: bytearray, out_w: int, out_h: int) -> bytearray:
    """the picture cropped to its subject, scaled to fit out_w x out_h and centred
    on a clear background. each output pixel averages the source pixels under it"""
    x0, y0, x1, y1 = _crop(width, height, rgba)
    cw, ch = x1 - x0, y1 - y0
    scale = min(out_w / cw, out_h / ch)
    sw, sh = max(1, round(cw * scale)), max(1, round(ch * scale))
    ox, oy = (out_w - sw) // 2, (out_h - sh) // 2
    out = bytearray(out_w * out_h * 4)
    xs = [(x0 + int(i * cw / sw), x0 + max(int(i * cw / sw) + 1, int((i + 1) * cw / sw))) for i in range(sw)]
    for j in range(sh):
        ya, yb = y0 + int(j * ch / sh), y0 + max(int(j * ch / sh) + 1, int((j + 1) * ch / sh))
        for i, (xa, xb) in enumerate(xs):
            r = g = b = a = 0
            for y in range(ya, yb):
                base = y * width * 4
                for x in range(xa, xb):
                    p = base + x * 4
                    pa = rgba[p + 3]
                    r += rgba[p] * pa
                    g += rgba[p + 1] * pa
                    b += rgba[p + 2] * pa
                    a += pa
            n = (yb - ya) * (xb - xa)
            q = ((oy + j) * out_w + ox + i) * 4
            if a:   # weight colour by alpha so edges don't go dark
                out[q:q + 4] = bytes((r // a, g // a, b // a, a // n))
    return out


# --- g-code ------------------------------------------------------------

def wanted_sizes(gcode: Path) -> list[tuple[int, int]]:
    """the png sizes the g-code's own settings ask for, e.g. [(96, 96), (300, 300)]"""
    with open(gcode, "rb") as fh:
        head = fh.read(256 * 1024)
        fh.seek(0, os.SEEK_END)
        fh.seek(max(0, fh.tell() - 256 * 1024))
        tail = fh.read()
    settings = {}
    for text in (head, tail):
        for m in re.finditer(rb"^; (thumbnails|thumbnails_format) = (.*?)\r?$", text, re.MULTILINE):
            settings[m.group(1).decode()] = m.group(2).decode("utf-8", "replace").strip()
    default = settings.get("thumbnails_format", "PNG").upper()
    sizes = []
    for m in _SIZE.finditer(settings.get("thumbnails", "")):
        w, h, kind = int(m.group(1)), int(m.group(2)), (m.group(3) or default).upper()
        if kind == "PNG" and 0 < w <= MAX_SIDE and 0 < h <= MAX_SIDE and (w, h) not in sizes:
            sizes.append((w, h))
    return sizes


def block(png: bytes, sizes: list[tuple[int, int]]) -> str:
    width, height, rgba = decode(png)
    parts = ["; THUMBNAIL_BLOCK_START\n"]
    for w, h in sizes:
        text = base64.b64encode(encode(w, h, fit(width, height, rgba, w, h))).decode()
        parts.append(f";\n; thumbnail begin {w}x{h} {len(text)}\n")
        parts += [f"; {text[i:i + LINE]}\n" for i in range(0, len(text), LINE)]
        parts.append("; thumbnail end\n;\n")
    parts.append("; THUMBNAIL_BLOCK_END\n\n")
    return "".join(parts)


def add(gcode: Path, project: Path, plate: int | None = None) -> bool:
    """write preview pictures into gcode (in place) from the project's plate picture.
    plate: 1-based, else read from the name plate_<n>.gcode. false if nothing was
    added: no sizes asked for, no picture, already has some, or anything odd.
    never raises"""
    try:
        return _add(Path(gcode), Path(project), plate)
    except Exception:
        return False


def _add(gcode: Path, project: Path, plate: int | None) -> bool:
    sizes = wanted_sizes(gcode)
    if not sizes:
        return False
    with open(gcode, "rb") as fh:
        head = fh.read(256 * 1024)
    if b"; thumbnail begin" in head or b"; THUMBNAIL_BLOCK_START" in head:
        return False
    if plate is None:
        m = _PLATE.search(gcode.name)
        plate = int(m.group(1)) if m else 1
    pictures = plate_pictures(project)
    if not pictures:
        return False
    png = pictures[plate - 1] if 0 < plate <= len(pictures) else pictures[0]
    text = block(png, sizes).encode()
    # after the header and the blank line under it, where orca puts them
    at = head.find(_HEAD)
    if at >= 0:
        at = head.index(b"\n", at) + 1
        if head[at:at + 2] == b"\r\n":
            at += 2
        elif head[at:at + 1] == b"\n":
            at += 1
    else:
        at = 0
    if b"\r\n" in head[:at or 4096]:
        text = text.replace(b"\n", b"\r\n")
    temp = gcode.with_name(gcode.name + ".tmp")
    with open(gcode, "rb") as src, open(temp, "wb") as dst:
        dst.write(src.read(at))
        dst.write(text)
        while (piece := src.read(1024 * 1024)):
            dst.write(piece)
    os.replace(temp, gcode)
    return True
