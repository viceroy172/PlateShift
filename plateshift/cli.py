from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .convert import ConversionError, analyse, convert, default_output
from .printers import BY_KEY, TARGETS
from .profiles import ProfileLibrary, locate, user_preset_dirs
from .slicers import find_slicers, test_slicer
from .testslice import run as test_slice


def _parse_override(text: str) -> tuple[int, str]:
    slot, sep, name = text.partition("=")
    if not sep or not slot.strip().isdigit() or not name.strip():
        raise argparse.ArgumentTypeError('use TOOLHEAD="Preset name", e.g. 2="Generic PLA Silk @FF C5P"')
    return int(slot), name.strip()


def _parse_colour(text: str) -> tuple[int, str]:
    slot, sep, colour = text.partition("=")
    if not sep or not slot.strip().isdigit() or not colour.strip():
        raise argparse.ArgumentTypeError("use TOOLHEAD=#RRGGBB, e.g. 3=#2E7D32")
    return int(slot), colour.strip()


def _parse_merge(text: str) -> dict[int, int]:
    # "5=2,6=3": colour 5 -> toolhead 2, colour 6 -> toolhead 3
    out = {}
    for part in text.split(","):
        a, sep, b = part.partition("=")
        if not sep or not a.strip().isdigit() or not b.strip().isdigit():
            raise argparse.ArgumentTypeError('use COLOUR=TOOLHEAD pairs, e.g. "5=2,6=3"')
        out[int(a)] = int(b)
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="plateshift",
                                     description="Convert Bambu Studio / MakerWorld 3MF projects for other printers, offline.")
    sub = parser.add_subparsers(dest="command", required=True)

    conv = sub.add_parser("convert", help="convert one or more .3mf projects")
    conv.add_argument("files", nargs="+", type=Path)
    conv.add_argument("-p", "--printer", default="c5p", choices=sorted(BY_KEY),
                      help="target printer (default: c5p)")
    conv.add_argument("-o", "--output", type=Path,
                      help="output file (one input) or folder (several inputs)")
    conv.add_argument("-f", "--filament", action="append", type=_parse_override, default=[],
                      metavar='TOOLHEAD="PRESET"', help="choose the filament preset for a toolhead")
    conv.add_argument("-c", "--colour", action="append", type=_parse_colour, default=[],
                      metavar="TOOLHEAD=#RRGGBB", help="show a toolhead in your spool's colour")
    conv.add_argument("-m", "--merge", type=_parse_merge, metavar="COLOUR=TOOLHEAD,...",
                      help="put project colours on toolheads yourself; the rest are matched automatically")
    conv.add_argument("-t", "--test-slice", action="store_true",
                      help="slice the result with Flash Studio to check it works")
    conv.add_argument("--profiles", type=Path, help="folder holding <Vendor>/machine, process, filament")

    ins = sub.add_parser("inspect", help="show a project's colours and how they would be mapped")
    ins.add_argument("file", type=Path)
    ins.add_argument("-p", "--printer", default="c5p", choices=sorted(BY_KEY))
    ins.add_argument("--profiles", type=Path)

    sub.add_parser("printers", help="list target printers")
    fil = sub.add_parser("filaments", help="list filament presets for a printer")
    fil.add_argument("-p", "--printer", default="c5p", choices=sorted(BY_KEY))
    fil.add_argument("--profiles", type=Path)
    return parser


def _library(target, profiles: Path | None):
    target, root = locate(target, [profiles] if profiles else None)
    return target, ProfileLibrary(root, target.vendor, user_preset_dirs())


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "printers":
        for t in TARGETS:
            print(f"{t.key:6} {t.label} ({t.toolheads} toolhead{'s' if t.toolheads > 1 else ''})")
        return 0

    target = BY_KEY[args.printer]
    try:
        target, lib = _library(target, args.profiles)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.command == "filaments":
        for p in lib.compatible("filament", target.machine, include_user=True):
            mine = f"  [yours, {p['_user']}]" if p.get("_user") else ""
            print(f"{p['name']}  ({p.get('filament_type', ['?'])[0]}){mine}")
        return 0

    if args.command == "inspect":
        try:
            plan = analyse(args.file, target, lib)
        except ConversionError as exc:
            print(exc, file=sys.stderr)
            return 1
        total = sum(f.usage for f in plan.filaments) or 1
        print(f"{args.file.name}: {len(plan.filaments)} colours, {target.label} has {target.toolheads}")
        for f, t in zip(plan.filaments, plan.merge):
            use = f"{100 * f.usage / total:3.0f}%" if f.used else "unused"
            print(f"  colour {f.index} {f.colour} {use:>6}  {f.name} ({f.type}) -> toolhead {t}")
        for t in plan.toolheads():
            print(f"  toolhead {t}: {plan.presets[t]}")
        return 0

    tester = test_slicer({s.label: s for s in find_slicers()}) if args.test_slice else None
    if args.test_slice and not tester:
        print("Neither Flash Studio nor OrcaSlicer was found, so --test-slice can't run.", file=sys.stderr)
        return 2

    overrides = dict(args.filament)
    failures = 0
    for source in args.files:
        if args.output and len(args.files) == 1 and args.output.suffix.lower() == ".3mf":
            output = args.output
        elif args.output:
            output = args.output / default_output(source, target).name
        else:
            output = default_output(source, target)
        try:
            plan = analyse(source, target, lib)
            merge = None
            if args.merge:
                merge = [args.merge.get(f.index, t) for f, t in zip(plan.filaments, plan.merge)]
            report = convert(source, output, target, lib, overrides, merge=merge, plan=plan,
                             colours=dict(args.colour))
        except (ConversionError, KeyError, OSError) as exc:
            failures += 1
            print(f"{source.name}: {exc}", file=sys.stderr)
            continue
        print("\n".join(report.lines()))
        if args.test_slice:
            result = test_slice(tester, output)
            print(f"  {result.line()}")
            failures += not result.ok
    return 1 if failures else 0
