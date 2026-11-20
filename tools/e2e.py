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


# PLA and PETG can't share one nozzle, so one-nozzle colour printers get this set
ALL_PLA = ["Bambu PLA Basic @BBL X1C", "Bambu PLA Matte @BBL X1C",
           "Bambu PLA Basic @BBL X1C", "Bambu PLA Basic @BBL X1C"]


def bambu_project(profiles: Path, filament_names: list[str] | None = None) -> dict:
    """the settings a Bambu Studio X1C project with four filaments would carry"""
    names = filament_names or SOURCE["filaments"]
    lib = ProfileLibrary(profiles, "BBL")
    machine = settings_only(lib.resolve("machine", SOURCE["machine"]))
    process = settings_only(lib.resolve("process", SOURCE["process"]))
    filaments = [settings_only(lib.resolve("filament", f)) for f in names]
    settings = {**machine, **process}
    for key in set().union(*filaments):
        settings[key] = [(f.get(key) or [""])[0] if isinstance(f.get(key), list) else f.get(key, "")
                         for f in filaments]
    settings.update(
        printer_settings_id=SOURCE["machine"], print_settings_id=SOURCE["process"],
        filament_settings_id=names, filament_colour=SOURCE["colours"],
        printer_model=machine.get("printer_model", "Bambu Lab X1 Carbon"),
        version="02.02.00.85", wall_loops="3", sparse_infill_density="25%",
        sparse_infill_pattern="gyroid", enable_support="1")
    return settings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles", type=Path, required=True, help="folder with BBL and target vendors")
    ap.add_argument("--slicer", type=Path, required=True, help="slicer program for test slicing")
    ap.add_argument("--printers", default="c5p,ad5x,ad5m,k1max")
    args = ap.parse_args()

    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        def project(name, filaments=None):
            return write_project(tmp / name, bambu_project(args.profiles, filaments),
                                 [[(60 + 40 * i, 128) for i in range(4)]], size=20,
                                 paint={i: paint.encode(i) for i in range(1, 5)})
        mixed, pla = project("bambu x1c project.3mf"), project("bambu x1c all pla.3mf", ALL_PLA)
        print(f"built two 4-colour {SOURCE['machine']} projects: PLA + PETG, and all PLA")
        for key in args.printers.split(","):
            try:
                target, root = locate(BY_KEY[key], [args.profiles])
            except FileNotFoundError as exc:
                print(f"  {key}: no profiles ({exc.args[0].splitlines()[0]})")
                failures += 1
                continue
            lib = ProfileLibrary(root, target.vendor)
            out = tmp / f"out-{key}.3mf"
            src = mixed
            if target.slot == "Slot":  # one nozzle, several colours
                warned = convert(mixed, tmp / f"mixed-{key}.3mf", target, lib).warnings
                ok = any("nozzle temperatures" in w for w in warned)
                print(f"  {'OK ' if ok else 'FAIL'} {key}: PLA + PETG on one nozzle is flagged")
                failures += not ok
                src = pla
            report = convert(src, out, target, lib)
            print("\n".join("  " + line for line in report.lines()))
            result = test_slice(Slicer("slicer", args.slicer), out)
            print(f"  {'OK ' if result.ok else 'FAIL'} {result.line()}")
            failures += not result.ok
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
