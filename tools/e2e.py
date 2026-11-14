"""End-to-end check against a real slicer install: build a realistic Bambu
Studio project from the slicer's own BBL profiles, convert it for each target
printer and test-slice the result.

    python tools/e2e.py --profiles <slicer profiles folder> --slicer <slicer program>

Used by CI on macOS (and handy on any machine with OrcaSlicer installed).
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from make_project import write_project  # noqa: E402

from plateshift import paint  # noqa: E402
from plateshift.convert import convert  # noqa: E402
from plateshift.printers import BY_KEY  # noqa: E402
from plateshift.profiles import ProfileLibrary, locate, settings_only  # noqa: E402
from plateshift.slicers import Slicer  # noqa: E402
from plateshift.testslice import run as test_slice  # noqa: E402

SOURCE = {
    "machine": "Bambu Lab X1 Carbon 0.4 nozzle",
    "process": "0.20mm Standard @BBL X1C",
    "filaments": ["Bambu PLA Basic @BBL X1C", "Bambu PLA Matte @BBL X1C",
                  "Bambu PETG HF @BBL X1C", "Bambu PLA Basic @BBL X1C"],
    "colours": ["#000000", "#FFFFFF", "#FF8000", "#00AA00"],
}


def bambu_project(profiles: Path) -> dict:
    """the settings a Bambu Studio X1C project with four filaments would carry"""
    lib = ProfileLibrary(profiles, "BBL")
    machine = settings_only(lib.resolve("machine", SOURCE["machine"]))
    process = settings_only(lib.resolve("process", SOURCE["process"]))
    filaments = [settings_only(lib.resolve("filament", f)) for f in SOURCE["filaments"]]
    settings = {**machine, **process}
    for key in set().union(*filaments):
        settings[key] = [(f.get(key) or [""])[0] if isinstance(f.get(key), list) else f.get(key, "")
                         for f in filaments]
    settings.update(
        printer_settings_id=SOURCE["machine"], print_settings_id=SOURCE["process"],
        filament_settings_id=SOURCE["filaments"], filament_colour=SOURCE["colours"],
        printer_model=machine.get("printer_model", "Bambu Lab X1 Carbon"),
        version="02.02.00.85", wall_loops="3", sparse_infill_density="25%",
        sparse_infill_pattern="gyroid", enable_support="1")
    return settings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles", type=Path, required=True, help="folder with BBL and target vendors")
    ap.add_argument("--slicer", type=Path, required=True, help="slicer program for test slicing")
    ap.add_argument("--printers", default="c5p,k1max")
    args = ap.parse_args()

    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = write_project(tmp / "bambu x1c project.3mf", bambu_project(args.profiles),
                            [[(60 + 40 * i, 128) for i in range(4)]], size=20,
                            paint={i: paint.encode(i) for i in range(1, 5)})
        print(f"built {src.name}: 4 painted colours, {SOURCE['machine']}")
        for key in args.printers.split(","):
            try:
                target, root = locate(BY_KEY[key], [args.profiles])
            except FileNotFoundError as exc:
                print(f"  {key}: no profiles ({exc.args[0].splitlines()[0]})")
                failures += 1
                continue
            lib = ProfileLibrary(root, target.vendor)
            out = tmp / f"out-{key}.3mf"
            report = convert(src, out, target, lib)
            print("\n".join("  " + line for line in report.lines()))
            result = test_slice(Slicer("slicer", args.slicer), out)
            print(f"  {'OK ' if result.ok else 'FAIL'} {result.line()}")
            failures += not result.ok
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
