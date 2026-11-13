"""Painted colour data in 3MF triangles (paint_color / mmu_segmentation).

Each triangle stores a bitstream as hex, last nibble first. The stream is a
tree: 2 bits split count, then either 2 bits special side + children, or a
leaf state. States 0-2 fit in 2 bits, 3+ are 11 followed by 4 bit chunks.
State 0 = unpainted (object's filament), n = filament n.
"""

from __future__ import annotations

import re


def _bits(code: str) -> list[int]:
    out = []
    for ch in reversed(code):
        v = int(ch, 16)
        out.extend((v >> i) & 1 for i in range(4))
    return out


def _hex(bits: list[int]) -> str:
    bits = bits + [0] * (-len(bits) % 4)
    nibbles = []
    for i in range(0, len(bits), 4):
        v = bits[i] | bits[i + 1] << 1 | bits[i + 2] << 2 | bits[i + 3] << 3
        nibbles.append("0123456789ABCDEF"[v])
    return "".join(reversed(nibbles))


def decode(code: str):
    bits = _bits(code)
    pos = 0

    def take(n):
        nonlocal pos
        v = 0
        for i in range(n):
            v |= (bits[pos + i] if pos + i < len(bits) else 0) << i
        pos += n
        return v

    def node():
        split = take(2)
        if split:
            special = take(2)
            return (split, special, [node() for _ in range(split + 1)])
        state = take(2)
        if state == 3:
            while True:
                chunk = take(4)
                state += chunk
                if chunk != 15:
                    break
        return state

    return node()


def encode(tree) -> str:
    bits: list[int] = []

    def put(v, n):
        bits.extend((v >> i) & 1 for i in range(n))

    def node(t):
        if isinstance(t, tuple):
            split, special, children = t
            put(split, 2)
            put(special, 2)
            for c in children:
                node(c)
            return
        put(0, 2)
        if t < 3:
            put(t, 2)
            return
        put(3, 2)
        n = t - 3
        while n >= 15:
            put(15, 4)
            n -= 15
        put(n, 4)

    node(tree)
    return _hex(bits)


def states(tree, into: set | None = None) -> set:
    into = set() if into is None else into
    if isinstance(tree, tuple):
        for c in tree[2]:
            states(c, into)
    else:
        into.add(tree)
    return into


def remap_tree(tree, mapping: dict[int, int]):
    if isinstance(tree, tuple):
        split, special, children = tree
        return (split, special, [remap_tree(c, mapping) for c in children])
    return mapping.get(tree, tree)


_PAINT = re.compile(r'(paint_color|slic3rpe:mmu_segmentation)="([0-9A-Fa-f]+)"')


def used_states(model_xml: str) -> dict[int, int]:
    """triangle count per painted state"""
    counts: dict[str, int] = {}
    for m in _PAINT.finditer(model_xml):
        counts[m.group(2)] = counts.get(m.group(2), 0) + 1
    totals: dict[int, int] = {}
    for code, n in counts.items():
        for s in states(decode(code.upper())):
            totals[s] = totals.get(s, 0) + n
    return totals


def remap_model(model_xml: str, mapping: dict[int, int]) -> tuple[str, int]:
    cache: dict[str, str] = {}
    changed = 0

    def sub(m):
        nonlocal changed
        code = m.group(2).upper()
        new = cache.get(code)
        if new is None:
            new = cache[code] = encode(remap_tree(decode(code), mapping))
        if new != code:
            changed += 1
        return f'{m.group(1)}="{new}"'

    return _PAINT.sub(sub, model_xml), changed
