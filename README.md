# PlateShift

Convert Bambu Studio and MakerWorld 3MF projects so they open ready to print on
another printer, entirely on your own PC. PlateShift never uses the internet: it
reads the printer profiles your slicer already installed and rewrites the project
file locally.

Supported targets (more are mostly a matter of adding a line to
`plateshift/printers.py`):

| Key     | Printer                  | Toolheads | Profiles from |
|---------|--------------------------|-----------|---------------|
| `c5p`   | Flashforge Creator 5 Pro | 4         | Flash Studio or OrcaSlicer |
| `c5`    | Flashforge Creator 5     | 4         | Flash Studio or OrcaSlicer |
| `k1max` | Creality K1 Max          | 1         | Creality Print |

## Requirements

- Windows
- Flash Studio or OcraSlicer installed, for the Flashforge printer profiles
  (Creality Print for the K1 Max)
- Either `PlateShift.exe`, or Python 3.11 or newer with Tk (standard library only,
  nothing to `pip install`)

## Use it

**Window:** run `PlateShift.exe` (or double-click `PlateShift.bat`), pick the
printer, then choose files or drop them on the window.

For a single file you get a panel listing the project's colours and how much of
the model each one paints. Put each colour on a toolhead (two colours on one
toolhead merges them) and pick a filament preset per toolhead, including your
own presets from Flash Studio and OrcaSlicer. Click a toolhead's colour to change it
to your spool's colour (the last ten you used are kept for quick picking); this only
changes the colour the slicer shows, not what gets printed. Projects with more colours than the
printer has toolheads start with the closest colours already merged. Several files
at once are matched automatically.

**Test slice** runs Flash Studio in the background after converting and adds the
print time and filament weight to the report, or the error if it won't slice.
**Then open in** opens a single converted file in a slicer that has the printer's
profiles: Flash Studio or OrcaSlicer for the Flashforge printers, Creality Print or
OrcaSlicer for the K1 Max. The choice is remembered per printer in
`%APPDATA%\PlateShift\settings.json`.

**Command line:**

```
python -m plateshift inspect "Model.3mf"                      # colours, usage, suggested toolheads
python -m plateshift convert "Model.3mf"                      # -> "Model - C5P.3mf"
python -m plateshift convert "Model.3mf" -m "5=2,6=3"         # colour 5 -> toolhead 2, 6 -> 3
python -m plateshift convert "Model.3mf" -c 3=#2E7D32         # show toolhead 3 in your green
python -m plateshift convert "Model.3mf" -f 1="FilX SBS @FF C5P" --test-slice
python -m plateshift convert *.3mf --printer k1max -o converted\
python -m plateshift filaments --printer c5p                  # preset names, yours marked
python -m plateshift printers
```

Open the result with **File → Open Project**. Importing it as a model leaves the
converted settings behind.

## What it changes

| Part of the project | What happens |
|---|---|
| Printer settings | Replaced by the target's system printer preset, start/end G-code included |
| Print settings | Taken from the target process preset with the closest layer height. Then the project's design choices (layer height, walls, infill, supports, raft, brim, ironing, seam, fuzzy skin, which filament prints what, and line widths when the nozzle size matches) are carried over. Everything else comes from the target preset or the slicer's defaults. |
| Filaments | Each toolhead gets the closest target preset of the same material, matched on type and name (Basic, Silk, Matte, CF…), or the one you pick, and keeps the project's colour |
| Painted colours | Rewritten triangle by triangle when colours are merged or moved to another toolhead, along with each object's filament, layer colour changes and support filament |
| Models, per-object settings, plates | Copied unchanged |
| Object positions | If the bed is a different size, objects and prime towers are moved so they stay where they were on their plate. Anything that then hangs off a plate or is too tall gets a warning |
| Sliced G-code | Removed, because it was made for the original printer |
| Bambu Studio 2.x extras | Per-nozzle-type lists are cut to the standard nozzle, `#D6B685FF` colours become `#D6B685`, Bambu-only options are dropped |
| Version label | Files from a newer Bambu Studio are labelled the way Flash Studio labels its own projects, so it opens them without a version warning |

The original file is never modified.

## Limits

- Your own printer presets are never used. They hold things like the printer's IP
  address and access key, which shouldn't end up in a project file.
- K1 Max projects use Creality Print's profiles. OrcaSlicer opens them as a project
  printer; switch to your own K1 Max preset there if you want its settings.
- Settings outside the carry-over list fall back to the target's values, so niche
  options the designer changed are left at their defualts.
- Only the 0.4 mm nozzle presets are targeted.
- OrcaSlicer's time estimate for the Creator 5 differs a lot from Flash Studio's;
  trust Flash Studio's for that printer.
- Check the first layer and supports in the the slicer before printing anything large.

## How it works

A 3MF project is a zip. `Metadata/project_settings.config` holds every slicer
setting as JSON, `Metadata/model_settings.config` holds objects and plates,
`3D/3dmodel.model` holds where each object sits and the `3D/Objects/*.model` files
hold the meshes, with painted colours stored per triangle as a small bit-packed tree
(`plateshift/paint.py`). PlateShift resolves the target presets through their
`inherits` chains (`plateshift/profiles.py`), builds new project settings and
rewrites the colours (`plateshift/convert.py`), checks fit (`plateshift/fit.py`)
and writes a new zip.

## Building the exe

```
py -3.11 -m venv .venv
.venv\Scripts\pip install pyinstaller
build_exe.bat
```

The result is `dist\PlateShift.exe`. Build with a Python that has Tk.

## Tests

```
python -m unittest discover -s tests
```

The tests build small Bambu-style projects in code. One extra test runs against
the real Flashforge presets when Flash Studio or OrcaSlicer is installed.

PlateShift is not affiliated with Bambu Lab, Flashforge, Creality or MakerWorld.

## Licence

MIT, see `LICENSE`.
