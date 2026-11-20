from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    key: str
    label: str
    vendor: str         # profile folder name
    machine: str
    default_process: str
    toolheads: int      # how many filaments it can print at once (toolheads, or slots on one nozzle)
    suffix: str         # added to output file names
    note: str = ""
    slicers: tuple[str, ...] = ("Flash Studio", "OrcaSlicer")  # offered under "Then open in"
    # other (machine, process) names the same printer goes by in other slicers
    alternatives: tuple[tuple[str, str], ...] = ()
    # what one filament position is called: "Toolhead", "Slot" (one nozzle fed by a
    # colour unit) or "Filament" (single colour). empty: worked out from toolheads
    slot: str = ""
    # version labels flash studio 2.4.2 writes; it rejects newer files
    file_version: str = "02.06.00.51"
    orca_version: str = "2.4.2"

    def preset_names(self) -> list[tuple[str, str]]:
        return [(self.machine, self.default_process), *self.alternatives]

    @property
    def slot_word(self) -> str:
        return self.slot or ("Toolhead" if self.toolheads > 1 else "Filament")


CREALITY_NOTE = ("Creality Print and OrcaSlicer name some printers differently, so a project made with "
                 "one slicer's profiles can open in the other as a project printer.")
CREALITY_PRINT = ("Creality Print",)
CFS_NOTE = "One nozzle fed by the CFS: colours sharing a slot print in that slot's filament."


def _creality(key: str, label: str, suffix: str, names: list[tuple[str, str]], colours: int = 1,
              slicers: tuple[str, ...] = ("Creality Print", "OrcaSlicer")) -> Target:
    """a creality printer; `names` lists (machine, process) preset names, orcaslicer's first,
    so projects line up with orca presets based on them, then creality print's"""
    (machine, process), *others = names
    multi = colours > 1
    return Target(key, label if multi else f"{label} (single colour)", "Creality", machine, process,
                  colours, suffix, note=CFS_NOTE if multi else CREALITY_NOTE,
                  slicers=slicers, alternatives=tuple(others),
                  slot="Slot" if multi else "")


def _same_names(machine: str, process: str) -> list[tuple[str, str]]:
    return [(machine, process)]


TARGETS = [
    Target("c5p", "Flashforge Creator 5 Pro", "Flashforge",
           "Flashforge Creator 5 Pro 0.4 nozzle", "0.20mm Standard @FF C5", 4, "C5P"),
    Target("c5", "Flashforge Creator 5", "Flashforge",
           "Flashforge Creator 5 0.4 nozzle", "0.20mm Standard @FF C5", 4, "C5"),
    # one nozzle, four colours through the built-in IFS
    Target("ad5x", "Flashforge AD5X", "Flashforge",
           "Flashforge AD5X 0.4 nozzle", "0.20mm Standard @FF AD5X", 4, "AD5X",
           note="One nozzle fed by the IFS: colours sharing a slot print in that slot's filament.",
           slot="Slot"),
    Target("ad5m", "Flashforge Adventurer 5M (single colour)", "Flashforge",
           "Flashforge Adventurer 5M 0.4 Nozzle", "0.20mm Standard @Flashforge AD5M 0.4 Nozzle", 1, "AD5M"),
    Target("ad5mpro", "Flashforge Adventurer 5M Pro (single colour)", "Flashforge",
           "Flashforge Adventurer 5M Pro 0.4 Nozzle", "0.20mm Standard @Flashforge AD5M Pro 0.4 Nozzle", 1,
           "AD5M Pro"),

    # creality K1 series, without and with the CFS colour unit
    _creality("k1", "Creality K1", "K1", [
        ("Creality K1 (0.4 nozzle)", "0.20mm Standard @Creality K1 (0.4 nozzle)"),
        ("Creality K1 0.4 nozzle", "0.20mm Standard @Creality K1 0.4 nozzle")]),
    _creality("k1c", "Creality K1C", "K1C", [
        ("Creality K1C 0.4 nozzle", "0.20mm Standard @Creality K1C"),
        ("Creality K1C 0.4 nozzle", "0.20mm Standard @Creality K1C 0.4 nozzle")]),
    _creality("k1se", "Creality K1 SE", "K1 SE",
              _same_names("Creality K1 SE 0.4 nozzle", "0.20mm Standard @Creality K1 SE")),
    _creality("k1max", "Creality K1 Max", "K1 Max", [
        ("Creality K1 Max (0.4 nozzle)", "0.20mm Standard @Creality K1Max (0.4 nozzle)"),
        ("Creality K1 Max 0.4 nozzle", "0.20mm Standard @Creality K1 Max 0.4 nozzle")]),
    _creality("k1cfs", "Creality K1 + CFS", "K1 CFS",
              _same_names("Creality K1_CFS-C 0.4 nozzle", "0.20mm Standard @Creality K1_CFS-C 0.4 nozzle"), 4),
    _creality("k1ccfs", "Creality K1C + CFS", "K1C CFS",
              _same_names("Creality K1C_CFS-C 0.4 nozzle", "0.20mm Standard @Creality K1C_CFS-C 0.4 nozzle"), 4),
    _creality("k1secfs", "Creality K1 SE + CFS", "K1 SE CFS",
              _same_names("Creality K1 SE_CFS-C 0.4 nozzle", "0.20mm Standard @Creality K1 SE_CFS-C 0.4 nozzle"), 4),
    _creality("k1maxcfs", "Creality K1 Max + CFS", "K1 Max CFS",
              _same_names("Creality K1 Max_CFS-C 0.4 nozzle", "0.20mm Standard @Creality K1 Max_CFS-C 0.4 nozzle"), 4),
    # the 2025 editions are only in creality print so far, and their g-code uses its own
    # variables, so orca can neither open nor test slice them
    _creality("k1c2025cfs", "Creality K1C 2025 + CFS", "K1C 2025 CFS",
              _same_names("Creality K1C 2025_CFS-C 0.4 nozzle",
                          "0.20mm Standard @Creality K1C 2025_CFS-C 0.4 nozzle"), 4, CREALITY_PRINT),
    _creality("k1max2025cfs", "Creality K1 Max 2025 + CFS", "K1 Max 2025 CFS",
              _same_names("Creality K1 Max 2025_CFS-C 0.4 nozzle",
                          "0.20mm Standard @Creality K1 Max 2025_CFS-C 0.4 nozzle"), 4, CREALITY_PRINT),

    # creality K2 series: one profile with or without CFS units; one CFS (4 colours) assumed
    _creality("k2", "Creality K2", "K2",
              _same_names("Creality K2 0.4 nozzle", "0.20mm Standard @Creality K2 0.4 nozzle"), 4),
    _creality("k2plus", "Creality K2 Plus", "K2 Plus",
              _same_names("Creality K2 Plus 0.4 nozzle", "0.20mm Standard @Creality K2 Plus 0.4 nozzle"), 4),
    _creality("k2pro", "Creality K2 Pro", "K2 Pro",
              _same_names("Creality K2 Pro 0.4 nozzle", "0.20mm Standard @Creality K2 Pro 0.4 nozzle"), 4),
    _creality("k2se", "Creality K2 SE", "K2 SE",
              _same_names("Creality K2 SE 0.4 nozzle", "0.20mm Standard @Creality K2 SE 0.4 nozzle"), 4),

    # creality sparkx: four colours assumed
    _creality("sparkxi7", "Creality SparkX i7", "SparkX i7", [
        ("Creality SPARKX i7 0.4 nozzle", "0.20mm Standard @Creality SPARKX i7 0.4 nozzle"),
        ("SPARKX i7 0.4 nozzle", "0.20mm Standard @SPARKX i7 0.4 nozzle")], 4),
    _creality("sparkxi8", "Creality SparkX i8", "SparkX i8",
              _same_names("SPARKX i8 0.4 nozzle", "0.20mm Standard @SPARKX i8 0.4 nozzle"), 4, CREALITY_PRINT),
]

BY_KEY = {t.key: t for t in TARGETS}
