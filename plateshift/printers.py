from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    key: str
    label: str
    vendor: str         # profile folder name
    machine: str
    default_process: str
    toolheads: int
    suffix: str         # added to output file names
    note: str = ""
    slicers: tuple[str, ...] = ("Flash Studio", "OrcaSlicer")  # offered under "Then open in"
    # other (machine, process) names the same printer goes by in other slicers
    alternatives: tuple[tuple[str, str], ...] = ()
    # version labels flash studio 2.4.2 writes; it rejects newer files
    file_version: str = "02.06.00.51"
    orca_version: str = "2.4.2"

    def preset_names(self) -> list[tuple[str, str]]:
        return [(self.machine, self.default_process), *self.alternatives]


TARGETS = [
    Target("c5p", "Flashforge Creator 5 Pro", "Flashforge",
           "Flashforge Creator 5 Pro 0.4 nozzle", "0.20mm Standard @FF C5", 4, "C5P"),
    Target("c5", "Flashforge Creator 5", "Flashforge",
           "Flashforge Creator 5 0.4 nozzle", "0.20mm Standard @FF C5", 4, "C5"),
    # orcaslicer's names first, so projects line up with orca presets based on them
    Target("k1max", "Creality K1 Max (single colour)", "Creality",
           "Creality K1 Max (0.4 nozzle)", "0.20mm Standard @Creality K1Max (0.4 nozzle)", 1, "K1 Max",
           note="Creality Print names this printer differently, so a project made with "
                "OrcaSlicer's profiles opens there as a project printer, and the other way round.",
           slicers=("Creality Print", "OrcaSlicer"),
           alternatives=(("Creality K1 Max 0.4 nozzle", "0.20mm Standard @Creality K1 Max 0.4 nozzle"),)),
]

BY_KEY = {t.key: t for t in TARGETS}
