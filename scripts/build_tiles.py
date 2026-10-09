#!/usr/bin/env python3
"""OBJ (one `o <objectid>` per building, Z-up, model CRS + header offset) -> 3D Tiles 1.1 with the building id per feature.
Usage: scripts/build_tiles.py KIPP_LOD1.obj OUT_DIR [EPSG=32750]
Names (`o` lines) MUST equal citydb.feature.objectid (gml:id). The writer is api/catalog/tiles.py (shared with the API,
which rebuilds tiles straight from the database after every edit). Needs numpy, pyproj, pygltflib."""
import re, sys, numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api" / "catalog"))
from tiles import write_tiles

obj, out = Path(sys.argv[1]), Path(sys.argv[2])
epsg = int(sys.argv[3]) if len(sys.argv) > 3 else 32750
V, F, names, owner, off = [], [], [], [], np.zeros(3)
for line in obj.read_text().splitlines():
    if line.startswith("# offset"): off = np.array([float(x) for x in re.findall(r"=(-?[\d.]+)", line)])
    elif line.startswith("o "): names.append(line[2:].strip())
    elif line.startswith("v "): V.append([float(x) for x in line.split()[1:4]])
    elif line.startswith("f "): F.append([int(t.split("/")[0]) - 1 for t in line.split()[1:4]]); owner.append(len(names) - 1)
V, F, owner = np.array(V) + off, np.array(F), np.array(owner)
assert F.max() < len(V), "bad OBJ"
write_tiles(V, F, owner, names, out, epsg)
print(f"{len(names)} buildings, {len(F)} triangles -> {out}")
