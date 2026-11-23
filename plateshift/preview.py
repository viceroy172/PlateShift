# SPDX-License-Identifier: AGPL-3.0-or-later
"""The picture a slicer saved inside a 3MF project, for showing before converting."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

MAX_PNG = 8 * 1024 * 1024
# bambu and orca save plate_<n>.png per plate; others a single thumbnail
_FALLBACKS = ("Metadata/thumbnail.png", "Auxiliaries/.thumbnails/thumbnail_middle.png",
              "Metadata/plate_1_small.png")
_PLATE = re.compile(r"^Metadata/plate_(\d+)\.png$", re.IGNORECASE)


def plate_pictures(path: Path) -> list[bytes]:
    """PNG data of each plate's picture, plate 1 first; empty if the project has none.
    never raises: a missing or odd picture just means no preview"""
    try:
        with zipfile.ZipFile(path) as z:
            infos = {i.filename: i for i in z.infolist() if i.file_size <= MAX_PNG}
            plates = sorted((int(m.group(1)), name) for name in infos if (m := _PLATE.match(name)))
            names = [name for _, name in plates] or [n for n in _FALLBACKS if n in infos][:1]
            return [data for data in (z.read(n) for n in names) if data.startswith(b"\x89PNG")]
    except (OSError, zipfile.BadZipFile, KeyError, ValueError, RuntimeError):
        return []
