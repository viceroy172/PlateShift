# PlateShift

Convert Bambu Studio and MakerWorld 3MF projects so they open ready to print on
another printer, entirely on your own PC. PlateShift never uses the internet: it
reads the printer profiles your slicer already installed and rewrites the project
file locally.

**Website and downloads: [cleland.co.za/PlateShift](https://cleland.co.za/PlateShift/)**

| Pick a printer | Choose toolheads and filaments | Converted |
|---|---|---|
| ![PlateShift window ready to convert](screenshots/main_screen.png) | ![Colours of a project mapped to toolheads and presets](screenshots/file_selected.png) | ![A finished conversion with its report](screenshots/done_results.png) |

Supported targets (more are mostly a matter of adding a line to
`plateshift/printers.py`):

| Key       | Printer                  | Colours at once | Profiles from |
|-----------|--------------------------|-----------------|---------------|
| `c5p`     | Flashforge Creator 5 Pro | 4 toolheads | Flash Studio or OrcaSlicer |
| `c5`      | Flashforge Creator 5     | 4 toolheads | Flash Studio or OrcaSlicer |
| `ad5x`    | Flashforge AD5X          | 4 IFS slots, one nozzle | Flash Studio or OrcaSlicer |
| `ad5m`    | Flashforge Adventurer 5M | 1 | Flash Studio or OrcaSlicer |
| `ad5mpro` | Flashforge Adventurer 5M Pro | 1 | Flash Studio or OrcaSlicer |
| `k1`, `k1c`, `k1se`, `k1max` | Creality K1, K1C, K1 SE, K1 Max | 1 | OrcaSlicer or Creality Print |
| `k1cfs`, `k1ccfs`, `k1secfs`, `k1maxcfs` | the same with the CFS | 4 CFS slots, one nozzle | OrcaSlicer or Creality Print |
| `k1c2025cfs`, `k1max2025cfs` | Creality K1C 2025, K1 Max 2025 with the CFS | 4 CFS slots, one nozzle | Creality Print |
| `k2`, `k2plus`, `k2pro`, `k2se` | Creality K2, K2 Plus, K2 Pro, K2 SE | 4 CFS slots, one nozzle | OrcaSlicer or Creality Print |
| `sparkxi7` | Creality SparkX i7 | 4 slots, one nozzle | OrcaSlicer or Creality Print |
| `sparkxi8` | Creality SparkX i8 | 4 slots, one nozzle | Creality Print |

On one-colour printers every colour prints in the one filament. On the AD5X and the
Creality CFS printers colours swap through one nozzle, so multi-colour prints take a
lot longer than on a toolchanger like the Creator 5. The K2 series assumes one CFS
(4 colours); projects with more colours are merged down to 4. The Creality K3 isn't
supported yet because no slicer ships a profile for it.

## Requirements

- Windows, Linux or macOS
- A slicer that has the printer's profiles: Flash Studio or OcraSlicer for the
  Flashforge printers, OrcaSlicer or Creality Print for the Creality ones. OrcaSlicer 2.4
  and later packs its profiles into `.opc` files; PlateShift reads those directly.
- On Windows, `PlateShift.exe`; on macOS, `PlateShift.app`. Both need nothing else
  installed. Running from source instead needs Python 3.11 or newer with Tk
  (standard library only, nothing to `pip install`).

## Windows

Download `PlateShift.exe` from the [latest release](https://github.com/viceroy172/PlateShift/releases/latest)
and run it. That's all: Python isn't needed. It isn't code-signed, so the first time
Windows SmartScreen may warn about it: choose **More info → Run anyway**.

`PlateShift.bat` in the source code is only for running PlateShift from source. That
needs Python 3.11 or newer from [python.org](https://www.python.org/downloads/windows/),
installed with **tcl/tk and IDLE** and the **py launcher** ticked (both are on by
default). If it can't find a suitable Python, `PlateShift.bat` says so and points you
back to the exe.

## Linux

```
sudo apt install python3 python3-tk        # Debian/Ubuntu; use your distro's equivalent
git clone https://github.com/viceroy172/PlateShift
cd PlateShift
./plateshift.sh                            # the window
./plateshift.sh convert "Model.3mf"        # or the command line
```

PlateShift looks for OrcaSlicer's profiles in `~/.config/OrcaSlicer/system` (and the
flatpak equivalent under `~/.var/app`). OrcaSlicer only puts a vendor there once
you've added one of its printers, so add your printer in OrcaSlicer first. If the
profiles live somewhere else, point PlateShift at them with `--profiles <folder>` or
the `PLATESHIFT_PROFILES` environment variable.

"Then open in" and the test slice find OrcaSlicer on your `PATH`, as a flatpak, or as
an AppImage in `~/Applications`, `~/Downloads`, `~/.local/bin` or `/opt`. Dropping files
on the window is Windows only; use **Choose 3MF files…** instead.

| Linux: ready | Choose toolheads and filaments | Converted and test sliced in OrcaSlicer |
|---|---|---|
| ![PlateShift on Linux, ready to convert](screenshots/linux_main_screen.png) | ![Colours mapped to toolheads on Linux](screenshots/linux_file_selected.png) | ![A finished conversion on Linux](screenshots/linux_done_results.png) |

## macOS

Download `PlateShift-mac.zip` from the [latest release](https://github.com/viceroy172/PlateShift/releases/latest),
unzip it and drag `PlateShift.app` to Applications. The app isn't signed by Apple,
so the first time macOS won't open it:

1. Double-click PlateShift. macOS says it can't verify the app; click **Done**.
2. Open **System Settings → Privacy & Security**, scroll down to the message about
   PlateShift and click **Open Anyway**, then confirm.

On macOS 14 (Sonoma) and earlier you can instead right-click the app, choose **Open**,
then **Open** again. Or, in Terminal: `xattr -dr com.apple.quarantine /Applications/PlateShift.app`.

If macOS says PlateShift **is damaged**, you have a 0.4.3 or 0.4.4 download from before
21 November 2026, whose signature was broken. Download it again.

Or run it from source with Python 3.11+ from python.org (Homebrew's Python needs
`brew install python-tk` as well):

```
git clone https://github.com/viceroy172/PlateShift
cd PlateShift
./plateshift.sh
```

PlateShift finds Flash Studio, OrcaSlicer and Creality Print in your Applications
folder and reads their profiles from `~/Library/Application Support`, or from inside
the app if you haven't added the printer there yet. Drop `.3mf` files on PlateShift's
Dock icon, or use **Open With → PlateShift** in Finder; dropping onto the window
itself isn't possible on macOS.

| macOS: ready | Choose toolheads and filaments | Converted and test sliced in OrcaSlicer |
|---|---|---|
| ![PlateShift on macOS, ready to convert](screenshots/mac_main_screen.png) | ![Colours mapped to toolheads on macOS](screenshots/mac_file_selected.png) | ![A finished conversion on macOS](screenshots/mac_done_results.png) |

The macOS build is tested automatically against the official OrcaSlicer for Mac, but
hasn't had much use on real Macs yet; reports are welcome.

## Use it

**Window:** run `PlateShift.exe` on Windows, `PlateShift.app` on macOS, or
`./plateshift.sh` on Linux (`PlateShift.bat` on Windows when running from source).
Pick the printer, then choose files or drop them on the window.

For a single file you get a panel listing the project's colours and how much of
the model each one paints. Put each colour on a toolhead (two colours on one
toolhead merges them) and pick a filament preset per toolhead, including your
own presets from Flash Studio and OrcaSlicer. Click a toolhead's colour to change it
to your spool's colour (the last ten you used are kept for quick picking); this only
changes the colour the slicer shows, not what gets printed. Projects with more colours than the
printer has toolheads start with the closest colours already merged. Several files
at once are matched automatically.

**Print profile** is Automatic by default: the slicer's own print preset with the
closest layer height, with the designer's choices put over it. Or pick one of your
own print profiles from Flash Studio, OrcaSlicer or Creality Print (marked *yours*) or
any of the slicer's. Then your profile wins: walls, infill, supports and so on come
from it, and only which filament prints what follows the project. Tick **Keep the
designer's layer height, walls, infill, supports…** to put the designer's choices over
your profile instead. A model can rely on the designer's settings (supports for
overhangs, or Classic walls for very thin parts), so if a test slice fails with your
profile, try ticking that box. The choice is remembered per printer.

**Test slice** runs Flash Studio for the Flashforge printers and OrcaSlicer for the
Creality ones (or OrcaSlicer wherever Flash Studio isn't installed) in the background
after converting, and adds the print time and filament weight to the report, or the
error if it won't slice. Creality Print can't slice from the command line, so testing a
Creality conversion needs OrcaSlicer.
**Then open in** opens a single converted file in a slicer that has the printer's
profiles: Flash Studio or OrcaSlicer for the Flashforge printers, Creality Print or
OrcaSlicer for the Creality ones. The choice is remembered per printer in
`%APPDATA%\PlateShift\settings.json` (`~/.config/plateshift/settings.json` on Linux and macOS).

**Command line:**

```
python -m plateshift inspect "Model.3mf"                      # colours, usage, suggested toolheads
python -m plateshift convert "Model.3mf"                      # -> "Model - C5P.3mf"
python -m plateshift convert "Model.3mf" -m "5=2,6=3"         # colour 5 -> toolhead 2, 6 -> 3
python -m plateshift convert "Model.3mf" -c 3=#2E7D32         # show toolhead 3 in your green
python -m plateshift convert "Model.3mf" -f 1="FilX SBS @FF C5P" --test-slice
python -m plateshift convert *.3mf --printer k1max -o converted\
python -m plateshift filaments --printer c5p                  # preset names, yours marked
python -m plateshift processes --printer c5p                  # print profiles, yours first
python -m plateshift convert "Model.3mf" --process "0.20mm Standard @FF C5P"   # your profile wins
python -m plateshift convert "Model.3mf" --process "0.20mm Standard @FF C5P" --keep-design
python -m plateshift printers
```

Open the result with **File → Open Project**. Importing it as a model leaves the
converted settings behind.

## What it changes

| Part of the project | What happens |
|---|---|
| Printer settings | Replaced by the target's system printer preset, start/end G-code included |
| Print settings | Taken from the print profile you pick, or by default the target process preset with the closest layer height. Then, unless your own pick wins, the project's design choices (layer height, walls, infill, supports, raft, brim, ironing, seam, fuzzy skin, which filament prints what, and line widths when the nozzle size matches) are carried over. Everything else comes from the target preset or the slicer's defaults. |
| Filaments | Each toolhead gets the closest target preset of the same material, matched on type and name (Basic, Silk, Matte, CF…), or the one you pick, and keeps the project's colour |
| Painted colours | Rewritten triangle by triangle when colours are merged or moved to another toolhead, along with each object's filament, layer colour changes and support filament |
| Models, per-object settings, plates | Copied unchanged |
| Object positions | If the bed is a different size, objects and prime towers are moved so they stay where they were on their plate. A prime tower that would then run into an object is moved to the nearest clear spot. Anything that hangs off a plate, is too tall or leaves no room for the tower gets a warning |
| Sliced G-code | Removed, because it was made for the original printer |
| Bambu Studio 2.x extras | Per-nozzle-type lists are cut to the standard nozzle, `#D6B685FF` colours become `#D6B685`, Bambu-only options are dropped |
| Version label | Every file is labelled the way Flash Studio labels its own projects, so it opens them without a "newer 3MF version" warning |

The original file is never modified.

## Limits

- Your own printer presets are never used. They hold things like the printer's IP
  address and access key, which shouldn't end up in a project file. Your own print
  and filament profiles are fine to use.
- Your own Creality Print profiles are only offered when PlateShift is using Creality
  Print's printer profiles, which it does when OrcaSlicer isn't installed. They're built
  on Creality Print's preset names, which OrcaSlicer's profiles don't have.
- Creality projects use OrcaSlicer's profiles when OrcaSlicer is installed, otherwise
  Creality Print's. The two name the printer differently, so a project opened in the
  other slicer shows up as a project printer.
- Settings outside the carry-over list fall back to the target's values, so niche
  options the designer changed are left at their defualts.
- Only the 0.4 mm nozzle presets are targeted.
- OrcaSlicer's time estimate for the Creator 5 differs a lot from Flash Studio's;
  trust Flash Studio's for that printer.
- The test slice fails for the Creality K2 and SparkX i7: OrcaSlicer's own profiles for
  them set a cutter retraction its command line rejects as out of range. PlateShift
  leaves the printer's value as Creality set it.
- The K1C 2025, K1 Max 2025 and SparkX i8 are only in Creality Print, which can't
  slice from the command line, so they can't be test sliced.
- On the Creality CFS printers the prime tower is large (around 65 mm square), so a
  model that nearly fills the bed leaves no room for it. PlateShift warns when that
  happens.
- Check the first layer and supports in the the slicer before printing anything large.

## How it works

A 3MF project is a zip. `Metadata/project_settings.config` holds every slicer
setting as JSON, `Metadata/model_settings.config` holds objects and plates,
`3D/3dmodel.model` holds where each object sits and the `3D/Objects/*.model` files
hold the meshes, with painted colours stored per triangle as a small bit-packed tree
(`plateshift/paint.py`). PlateShift resolves the target presets through their
`inherits` chains (`plateshift/profiles.py`), reading OrcaSlicer's packed `.opc`
profile files itself where needed (`plateshift/opc.py`, written from the layout in
OrcaSlicer's `docs/HLSD/preset-cache.md`), builds new project settings and rewrites
the colours (`plateshift/convert.py`), checks fit (`plateshift/fit.py`) and writes a
new zip.

## Building the apps

```
py -3.11 -m venv .venv
.venv\Scripts\pip install pyinstaller
build_exe.bat
```

The result is `dist\PlateShift.exe`. Build with a Python that has Tk.

`PlateShift.app` is built on a Mac by the `macos` GitHub workflow (the `app` job), which
keeps it as a downloadable `PlateShift-mac` artifact.

## Tests

```
python -m unittest discover -s tests
```

The tests build small Bambu-style projects in code. One extra test runs against
the real Flashforge presets when Flash Studio or OrcaSlicer is installed.

PlateShift is not affiliated with Bambu Lab, Flashforge, Creality or MakerWorld.

## Contact

Bugs and ideas are best raised as [GitHub issues](https://github.com/viceroy172/PlateShift/issues).
For anything else, email viceroy@imaginet.co.za.

## Licence

Copyright (C) 2026 PlateShift contributors.

PlateShift is free software: you can redistribute it and/or modify it under the
terms of the GNU Affero General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. It comes with no warranty. See `LICENSE` for the full terms.

Versions up to and including 0.4.4 were released under the MIT licence, and copies
of those versions keep it.
