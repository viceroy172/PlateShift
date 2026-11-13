"""Check every object sits inside its plate after conversion."""

from __future__ import annotations

import re
from itertools import product

_TOKENS = re.compile(
    r'<object\b[^>]*?\bid="(\d+)"'
    r'|<vertex\s+x="([^"]+)"\s+y="([^"]+)"\s+z="([^"]+)"'
    r'|<component\b([^>]*)>'
    r'|(</object>)')
_ATTR = re.compile(r'([\w:]+)="([^"]*)"')
_ITEM = re.compile(r"<item\b([^>]*)>")

IDENTITY = [1.0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]


def _matrix(text: str | None) -> list[float]:
    if not text:
        return IDENTITY
    nums = [float(n) for n in text.split()]
    return nums if len(nums) == 12 else IDENTITY


def _apply(m, p):
    # 3mf matrices are row vectors: p' = p * M
    x, y, z = p
    return (x * m[0] + y * m[3] + z * m[6] + m[9],
            x * m[1] + y * m[4] + z * m[7] + m[10],
            x * m[2] + y * m[5] + z * m[8] + m[11])


def parse_objects(xml: str, path: str) -> dict:
    """(path, id) -> ('mesh', bbox) or ('parts', [(path, id, matrix)])"""
    objects = {}
    current = None
    lo = hi = None
    parts: list = []
    for m in _TOKENS.finditer(xml):
        if m.group(1):
            current, lo, hi, parts = m.group(1), None, None, []
        elif m.group(2) is not None and current:
            p = (float(m.group(2)), float(m.group(3)), float(m.group(4)))
            if lo is None:
                lo, hi = list(p), list(p)
            else:
                for i in range(3):
                    if p[i] < lo[i]:
                        lo[i] = p[i]
                    elif p[i] > hi[i]:
                        hi[i] = p[i]
        elif m.group(5) is not None and current:
            a = dict(_ATTR.findall(m.group(5)))
            parts.append((a.get("p:path", path).lstrip("/"), a.get("objectid", ""), _matrix(a.get("transform"))))
        elif m.group(6) and current:
            objects[(path, current)] = ("mesh", (lo, hi)) if lo is not None else ("parts", parts)
            current = None
    return objects


def _points(objects, key, matrix, depth=0):
    entry = objects.get(key)
    if entry is None or depth > 5:
        return []
    if entry[0] == "mesh":
        lo, hi = entry[1]
        return [_apply(matrix, c) for c in product(*zip(lo, hi))]
    pts = []
    for path, oid, sub in entry[1]:
        for p in _points(objects, (path, oid), sub, depth + 1):
            pts.append(_apply(matrix, p))
    return pts


def check(model_files: dict[str, str], main_path: str, plate_of: dict, plate_centre,
          box, height: float, names: dict[str, str]) -> list[str]:
    """model_files: path -> xml. plate_centre(index) -> (x, y) of that plate's middle."""
    objects = {}
    for path, xml in model_files.items():
        objects.update(parse_objects(xml, path))

    warnings = []
    seen: dict[str, int] = {}
    half_w, half_d = (box[2] - box[0]) / 2, (box[3] - box[1]) / 2
    for m in _ITEM.finditer(model_files[main_path]):
        a = dict(_ATTR.findall(m.group(1)))
        oid = a.get("objectid", "")
        instance = seen.get(oid, 0)
        seen[oid] = instance + 1
        pts = _points(objects, (main_path, oid), _matrix(a.get("transform")))
        if not pts:
            continue
        plate = plate_of.get((oid, str(instance)), 0)
        cx, cy = plate_centre(plate)
        xs, ys, zs = zip(*pts)
        over = max(cx - half_w - min(xs), max(xs) - cx - half_w,
                   cy - half_d - min(ys), max(ys) - cy - half_d)
        name = names.get(oid, f"object {oid}")
        if over > 0.5:
            warnings.append(f"{name} sticks out of plate {plate + 1} by about {over:.0f} mm")
        if height and max(zs) - min(zs) > height + 0.5:
            warnings.append(f"{name} is {max(zs) - min(zs):.0f} mm tall, the printer takes {height:.0f} mm")
    return warnings
