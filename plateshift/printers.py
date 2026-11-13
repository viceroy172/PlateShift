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
    # version labels flash studio 2.4.2 writes; it rejects newer files
    file_version: str = "02.06.00.51"
    orca_version: str = "2.4.2"


TARGETS = [
    Target("c5p", "Flashforge Creator 5 Pro", "Flashforge",
           "Flashforge Creator 5 Pro 0.4 nozzle", "0.20mm Standard @FF C5", 4, "C5P"),
    Target("c5", "Flashforge Creator 5", "Flashforge",
           "Flashforge Creator 5 0.4 nozzle", "0.20mm Standard @FF C5", 4, "C5"),
    Target("k1max", "Creality K1 Max (single colour)", "Creality",
           "Creality K1 Max 0.4 nozzle", "0.20mm Standard @Creality K1 Max 0.4 nozzle", 1, "K1 Max",
           note="Uses Creality Print's K1 Max profiles. In OrcaSlicer it opens as a project "
                "printer; switch to your own K1 Max preset if you want its settings.",
           slicers=("Creality Print", "OrcaSlicer")),
]

BY_KEY = {t.key: t for t in TARGETS}
