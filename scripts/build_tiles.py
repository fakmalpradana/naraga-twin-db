#!/usr/bin/env python3
"""OBJ (one `o <objectid>` per building, Z-up, UTM + header offset) -> 3D Tiles 1.1 (one glb tile) with the
building id stored per feature, so the frontend can click a building and read its row from the database.
Usage: scripts/build_tiles.py KIPP_LOD1.obj OUT_DIR [EPSG=32750]
Names (`o` lines) MUST equal citydb.feature.objectid (gml:id). Needs numpy, pyproj, pygltflib."""
import json, re, sys, numpy as np
from pathlib import Path
from pyproj import Transformer
from pygltflib import GLTF2, Scene, Node, Mesh, Primitive, Buffer, BufferView, Accessor, Material, Asset

obj, out = Path(sys.argv[1]), Path(sys.argv[2])
epsg = int(sys.argv[3]) if len(sys.argv) > 3 else 32750
V, F, names, face_owner, cur = [], [], [], [], -1
off = np.zeros(3)
for line in obj.read_text().splitlines():
    if line.startswith("# offset"):
        off = np.array([float(x) for x in re.findall(r"=(-?[\d.]+)", line)])
    elif line.startswith("o "): names.append(line[2:].strip()); cur += 1
    elif line.startswith("v "): V.append([float(x) for x in line.split()[1:4]])
    elif line.startswith("f "):
        F.append([int(t.split("/")[0]) - 1 for t in line.split()[1:4]]); face_owner.append(cur)
V, F, owner = np.array(V) + off, np.array(F), np.array(face_owner)
assert F.max() < len(V) and len(names) == len(set(names)), "bad OBJ"

# UTM -> ECEF -> local ENU at the centre (glTF is Y-up: (e, u, -n); 3D Tiles turns it back to Z-up)
ecef = np.column_stack(Transformer.from_crs(epsg, 4978, always_xy=True).transform(*V.T))
c = ecef.mean(0); lon, lat = np.radians(Transformer.from_crs(4978, 4326, always_xy=True).transform(*c)[:2])
E = np.array([-np.sin(lon), np.cos(lon), 0]); N = np.array([-np.sin(lat)*np.cos(lon), -np.sin(lat)*np.sin(lon), np.cos(lat)])
U = np.array([np.cos(lat)*np.cos(lon), np.cos(lat)*np.sin(lon), np.sin(lat)])
d = ecef - c; enu = np.column_stack([d @ E, d @ N, d @ U])
tri = enu[F]                                        # (T,3,3) non-indexed -> flat per-face normals
nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]); nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
gl = lambda a: np.stack([a[..., 0], a[..., 2], -a[..., 1]], -1).astype("<f4")
pos, nor = gl(tri).reshape(-1, 3), np.repeat(gl(nrm), 3, axis=0)
fid = np.repeat(owner, 3).astype("<f4")

ids = [n.encode() for n in names]
offs = np.cumsum([0] + [len(b) for b in ids]).astype("<u4")
chunks = [("pos", pos.tobytes()), ("nor", nor.tobytes()), ("fid", fid.tobytes()),
          ("str", b"".join(ids)), ("off", offs.tobytes())]
blob, bvs, o = b"", {}, 0
g = GLTF2(asset=Asset(version="2.0"), scene=0, scenes=[Scene(nodes=[0])], nodes=[Node(mesh=0)])
for k, b in chunks:
    b += b"\0" * (-len(b) % 4); g.bufferViews.append(BufferView(buffer=0, byteOffset=len(blob), byteLength=len(b), target=34962 if k in ("pos","nor","fid") else None))
    bvs[k] = len(g.bufferViews) - 1; blob += b
n = len(pos)
g.accessors = [Accessor(bufferView=bvs["pos"], componentType=5126, count=n, type="VEC3", min=pos.min(0).tolist(), max=pos.max(0).tolist()),
               Accessor(bufferView=bvs["nor"], componentType=5126, count=n, type="VEC3"),
               Accessor(bufferView=bvs["fid"], componentType=5126, count=n, type="SCALAR")]
g.materials = [Material(pbrMetallicRoughness={"baseColorFactor": [0.85, 0.82, 0.75, 1], "metallicFactor": 0, "roughnessFactor": 1}, doubleSided=True)]
prim = Primitive(attributes={"POSITION": 0, "NORMAL": 1, "_FEATURE_ID_0": 2}, material=0)
prim.extensions = {"EXT_mesh_features": {"featureIds": [{"featureCount": len(names), "attribute": 0, "propertyTable": 0}]}}
g.meshes = [Mesh(primitives=[prim])]
g.buffers = [Buffer(byteLength=len(blob))]
g.extensionsUsed = ["EXT_mesh_features", "EXT_structural_metadata"]
g.extensions = {"EXT_structural_metadata": {
    "schema": {"id": "buildings", "classes": {"building": {"properties": {"objectid": {"type": "STRING"}}}}},
    "propertyTables": [{"class": "building", "count": len(names),
                        "properties": {"objectid": {"values": bvs["str"], "stringOffsets": bvs["off"], "stringOffsetType": "UINT32"}}}]}}
g.set_binary_blob(blob)

out.mkdir(parents=True, exist_ok=True)
g.save_binary(str(out / "buildings.glb"))
lo, hi = enu.min(0), enu.max(0); ctr, half = (lo + hi) / 2, (hi - lo) / 2
tx = [*E, 0, *N, 0, *U, 0, *c, 1]                  # column-major ENU -> ECEF
(out / "tileset.json").write_text(json.dumps({
    "asset": {"version": "1.1"}, "geometricError": 500,
    "root": {"boundingVolume": {"box": [*ctr, half[0], 0, 0, 0, half[1], 0, 0, 0, half[2]]}, "geometricError": 0,
             "refine": "ADD", "transform": tx, "content": {"uri": "buildings.glb"}}}))
print(f"{len(names)} buildings, {len(F)} triangles -> {out}")
