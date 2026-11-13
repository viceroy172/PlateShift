from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="png" ContentType="image/png"/>
 <Default Extension="gcode" ContentType="text/x.gcode"/>
</Types>
"""

RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""

CUBE_MESH = """<mesh><vertices>
<vertex x="0" y="0" z="0"/><vertex x="10" y="0" z="0"/><vertex x="10" y="10" z="0"/><vertex x="0" y="10" z="0"/>
<vertex x="0" y="0" z="10"/><vertex x="10" y="0" z="10"/><vertex x="10" y="10" z="10"/><vertex x="0" y="10" z="10"/>
</vertices><triangles>
<triangle v1="0" v2="2" v3="1"/><triangle v1="0" v2="3" v3="2"/><triangle v1="4" v2="5" v3="6"/><triangle v1="4" v2="6" v3="7"/>
<triangle v1="0" v2="1" v3="5"/><triangle v1="0" v2="5" v3="4"/><triangle v1="1" v2="2" v3="6"/><triangle v1="1" v2="6" v3="5"/>
<triangle v1="2" v2="3" v3="7"/><triangle v1="2" v2="7" v3="6"/><triangle v1="3" v2="0" v3="4"/><triangle v1="3" v2="4" v3="7"/>
</triangles></mesh>"""


def _paint_triangles(mesh: str, code: str) -> str:
    return re.sub(r'(<triangle [^/]*)/>', rf'\1 paint_color="{code}"/>', mesh)


def bambu_settings(bed: int = 256, **overrides) -> dict:
    # cut-down X1C project, 4 filaments
    settings = {
        "printer_model": "Bambu Lab X1 Carbon",
        "printer_settings_id": "Bambu Lab X1 Carbon 0.4 nozzle",
        "print_settings_id": "0.16mm Optimal @BBL X1C",
        "filament_settings_id": ["Bambu PLA Basic @BBL X1C", "Bambu PLA Silk @BBL X1C",
                                 "Bambu PETG HF @BBL X1C", "Generic PLA @BBL X1C"],
        "filament_type": ["PLA", "PLA", "PETG", "PLA"],
        "filament_colour": ["#000000", "#C0C0C0", "#FF8000", "#FFFFFF"],
        "filament_ids": ["GFA00", "GFA05", "GFG02", "GFL99"],
        "nozzle_temperature": ["220", "230", "255", "220"],
        "nozzle_diameter": ["0.4"],
        "printable_area": ["0x0", f"{bed}x0", f"{bed}x{bed}", f"0x{bed}"],
        "printable_height": "250",
        "machine_start_gcode": "M620 S0 ; Bambu AMS start",
        "layer_height": "0.16",
        "wall_loops": "3",
        "sparse_infill_density": "25%",
        "sparse_infill_pattern": "gyroid",
        "enable_support": "1",
        "support_type": "tree(auto)",
        "brim_type": "no_brim",
        "outer_wall_speed": "200",
        "line_width": "0.42",
        "inherits_group": ["0.16mm Optimal @BBL X1C", "", "", "", "", ""],
        "version": "02.02.00.85",
        "from": "project",
    }
    settings.update(overrides)
    return settings


def write_project(path: Path, settings: dict | None, plates: list[list[tuple[float, float]]],
                  gcode: bool = False, paint: dict[int, str] | None = None,
                  size: float = 10) -> Path:
    # plates: list of (x, y) cube positions per plate
    # paint: object id -> paint_color code put on every triangle of that cube
    objects, items, plate_xml = [], [], []
    object_id = 0
    for plate_index, positions in enumerate(plates, start=1):
        instances = []
        for x, y in positions:
            object_id += 1
            mesh = CUBE_MESH.replace('="10"', f'="{size:g}"')
            if paint and object_id in paint:
                mesh = _paint_triangles(mesh, paint[object_id])
            objects.append(f'<object id="{object_id}" type="model">{mesh}</object>')
            items.append(f'<item objectid="{object_id}" transform="1 0 0 0 1 0 0 0 1 {x} {y} 5" printable="1"/>')
            instances.append(
                f'<model_instance><metadata key="object_id" value="{object_id}"/>'
                f'<metadata key="instance_id" value="0"/></model_instance>')
        gcode_meta = f'<metadata key="gcode_file" value="Metadata/plate_{plate_index}.gcode"/>' if gcode else ""
        plate_xml.append(f'<plate><metadata key="plater_id" value="{plate_index}"/>{gcode_meta}'
                         + "".join(instances) + "</plate>")

    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
             ' <metadata name="Application">BambuStudio-02.02.00.85</metadata>\n'
             f' <resources>{"".join(objects)}</resources>\n'
             f' <build>{"".join(items)}</build>\n</model>\n')
    model_settings = ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n'
                      + "".join(f'<object id="{i}"><metadata key="extruder" value="{(i - 1) % 4 + 1}"/></object>'
                                for i in range(1, object_id + 1))
                      + "".join(plate_xml) + "\n</config>\n")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("3D/3dmodel.model", model)
        if settings is not None:
            z.writestr("Metadata/project_settings.config", json.dumps(settings, indent=4))
        z.writestr("Metadata/model_settings.config", model_settings)
        if gcode:
            for i in range(1, len(plates) + 1):
                z.writestr(f"Metadata/plate_{i}.gcode", "G28 ; sliced for X1C\n")
                z.writestr(f"Metadata/plate_{i}.gcode.md5", "0" * 32)
            z.writestr("Metadata/_rels/model_settings.config.rels",
                       '<?xml version="1.0" encoding="UTF-8"?>\n'
                       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
                       + "".join(f' <Relationship Target="/Metadata/plate_{i}.gcode" Id="rel-{i}" '
                                 'Type="http://schemas.bambulab.com/package/2021/gcode"/>\n'
                                 for i in range(1, len(plates) + 1))
                       + "</Relationships>\n")
    return path
