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


def hull(points) -> list[tuple[float, float]]:
    """convex hull of 2d points, anticlockwise"""
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def half(seq):
        out: list = []
        for p in seq:
            while len(out) >= 2 and ((out[-1][0] - out[-2][0]) * (p[1] - out[-2][1])
                                     - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0])) <= 0:
                out.pop()
            out.append(p)
        return out
    lower, upper = half(pts), half(reversed(pts))
    return lower[:-1] + upper[:-1]


def parse_objects(xml: str, path: str) -> dict:
    """(path, id) -> ('mesh', bbox, outline) or ('parts', [(path, id, matrix)]).
    outline: the convex hull of the mesh seen from above"""
    objects = {}
    current = None
    lo = hi = None
    parts: list = []
    flat: list = []
    for m in _TOKENS.finditer(xml):
        if m.group(1):
            current, lo, hi, parts, flat = m.group(1), None, None, [], []
        elif m.group(2) is not None and current:
            p = (float(m.group(2)), float(m.group(3)), float(m.group(4)))
            flat.append((p[0], p[1]))
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
            objects[(path, current)] = ("mesh", (lo, hi), hull(flat)) if lo is not None else ("parts", parts)
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


def _flat(m) -> bool:
    """True if the matrix only turns about z, so a mesh's outline from above moves with it"""
    return all(abs(m[i]) < 1e-9 for i in (2, 5, 6, 7))


def _compose(inner, outer):
    """the matrix applying inner, then outer"""
    rows = [inner[0:3], inner[3:6], inner[6:9], inner[9:12]]
    out = []
    for r in rows:
        out += [r[0] * outer[0] + r[1] * outer[3] + r[2] * outer[6],
                r[0] * outer[1] + r[1] * outer[4] + r[2] * outer[7],
                r[0] * outer[2] + r[1] * outer[5] + r[2] * outer[8]]
    for i in range(3):
        out[9 + i] += outer[9 + i]
    return out


def _outline(objects, key, matrix, depth=0):
    """2d points whose convex hull covers the object seen from above"""
    entry = objects.get(key)
    if entry is None or depth > 5:
        return []
    if entry[0] == "mesh":
        if _flat(matrix):
            return [_apply(matrix, (x, y, 0.0))[:2] for x, y in entry[2]]
        lo, hi = entry[1]
        return [_apply(matrix, c)[:2] for c in product(*zip(lo, hi))]
    pts = []
    for path, oid, sub in entry[1]:
        pts += _outline(objects, (path, oid), _compose(sub, matrix), depth + 1)
    return pts


def _parse_all(model_files: dict[str, str]) -> dict:
    objects = {}
    for path, xml in model_files.items():
        objects.update(parse_objects(xml, path))
    return objects


def _items(model_files: dict[str, str], main_path: str, plate_of: dict, objects: dict | None = None):
    """(object id, plate, matrix, corner points) for every build item"""
    if objects is None:
        objects = _parse_all(model_files)
    seen: dict[str, int] = {}
    for m in _ITEM.finditer(model_files[main_path]):
        a = dict(_ATTR.findall(m.group(1)))
        oid = a.get("objectid", "")
        instance = seen.get(oid, 0)
        seen[oid] = instance + 1
        matrix = _matrix(a.get("transform"))
        pts = _points(objects, (main_path, oid), matrix)
        if pts:
            yield oid, plate_of.get((oid, str(instance)), 0), matrix, pts


def footprints(model_files: dict[str, str], main_path: str, plate_of: dict, plate_centre,
               box) -> list[tuple[int, list[tuple[float, float]], float]]:
    """(plate, outline, height) of every object: its convex outline seen from above,
    anticlockwise in that plate's bed coordinates"""
    objects = _parse_all(model_files)
    mid_x, mid_y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    found = []
    for oid, plate, matrix, pts in _items(model_files, main_path, plate_of, objects):
        cx, cy = plate_centre(plate)
        flat = _outline(objects, (main_path, oid), matrix) or [p[:2] for p in pts]
        zs = [p[2] for p in pts]
        found.append((plate, hull((x - cx + mid_x, y - cy + mid_y) for x, y in flat), max(zs) - min(zs)))
    return found


def check(model_files: dict[str, str], main_path: str, plate_of: dict, plate_centre,
          box, height: float, names: dict[str, str]) -> list[str]:
    """model_files: path -> xml. plate_centre(index) -> (x, y) of that plate's middle."""
    warnings = []
    half_w, half_d = (box[2] - box[0]) / 2, (box[3] - box[1]) / 2
    for oid, plate, _, pts in _items(model_files, main_path, plate_of):
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
