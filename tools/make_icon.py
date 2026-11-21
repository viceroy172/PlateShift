"""Draw PlateShift's icon (the website's logo) and write every format the apps need.

Needs Pillow, only for this script: python tools/make_icon.py
Writes packaging/icon.png, icon.ico (windows exe), icon.icns (mac app) and
plateshift/icon_data.py (small PNGs the window uses, so no image files ship).
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
BLUE, DEEP = (47, 129, 247), (26, 79, 168)


def draw(size: int = 1024) -> Image.Image:
    big = size * 4  # draw large, then shrink for smooth edges
    unit = big / 32  # the logo is drawn on a 32 x 32 grid, like the website's favicon

    # diagonal gradient, as on the website's logo
    gradient = Image.new("RGB", (big, big))
    px = gradient.load()
    for y in range(big):
        for x in range(0, big, 4):
            t = (x + y) / (2 * big)
            colour = tuple(round(a + (b - a) * t) for a, b in zip(BLUE, DEEP))
            for dx in range(4):
                px[x + dx, y] = colour
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big - 1, big - 1), radius=round(8 * unit), fill=255)
    icon = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    icon.paste(gradient, (0, 0), mask)

    # two arrows passing each other: M7 11h15l-4-4  and  M25 21H10l4 4
    pen = ImageDraw.Draw(icon)
    width = round(3 * unit)
    for path in ([(7, 11), (22, 11), (18, 7)], [(25, 21), (10, 21), (14, 25)]):
        points = [(x * unit, y * unit) for x, y in path]
        pen.line(points, fill="white", width=width, joint="curve")
        for x, y in points:  # round caps
            r = width / 2
            pen.ellipse((x - r, y - r, x + r, y + r), fill="white")
    return icon.resize((size, size), Image.LANCZOS)


def png(image: Image.Image, size: int) -> bytes:
    out = io.BytesIO()
    image.resize((size, size), Image.LANCZOS).save(out, "PNG", optimize=True)
    return out.getvalue()


def main() -> None:
    icon = draw(1024)
    packaging = ROOT / "packaging"
    icon.save(packaging / "icon.png")
    icon.save(packaging / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    icon.save(packaging / "icon.icns")
    sizes = (16, 32, 48, 64, 128)
    lines = ['"""PlateShift\'s icon as small PNGs, made by tools/make_icon.py. Don\'t edit by hand."""', "",
             "ICONS = {"]
    for s in sizes:
        lines.append(f'    {s}: "{base64.b64encode(png(icon, s)).decode()}",')
    lines += ["}", ""]
    (ROOT / "plateshift" / "icon_data.py").write_text("\n".join(lines), encoding="utf-8")
    print("wrote", ", ".join(str(p.relative_to(ROOT)) for p in (
        packaging / "icon.png", packaging / "icon.ico", packaging / "icon.icns",
        ROOT / "plateshift" / "icon_data.py")))


if __name__ == "__main__":
    main()
