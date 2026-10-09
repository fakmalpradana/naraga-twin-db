"""3D Tiles writer shared by the API (rebuild from the database) and scripts/build_tiles.py (from an OBJ).
Input is a triangle mesh in the model CRS plus one name (= objectid) per building; output is a one-tile
tileset (tileset.json + buildings.glb) in which every building carries its `objectid`.
Pure functions, no Django imports. Needs numpy, pyproj, pygltflib, mapbox-earcut."""
import json
from pathlib import Path

import numpy as np
from pyproj import Transformer
from pygltflib import GLTF2, Scene, Node, Mesh, Primitive, Buffer, BufferView, Accessor, Material, Asset


def triangulate(faces):
    """faces: iterable of (building_index, rings) with rings = [exterior, *holes], each a list of (x, y, z).
    Returns (vertices Nx3, triangles Tx3, owner T). Earcut in the face's own plane, winding kept with the face normal."""
    import mapbox_earcut as earcut
    V, F, O = [], [], []
    for owner, rings in faces:
        pts = [p[:3] for r in rings for p in (r[:-1] if r[0] == r[-1] else r)]
        a = np.array(pts, dtype=float)
        ring_ends, n = [], 0
        for r in rings:
            n += len(r) - 1 if r[0] == r[-1] else len(r); ring_ends.append(n)
        ex = np.array(rings[0][:-1] if rings[0][0] == rings[0][-1] else rings[0], dtype=float)
        nrm = np.zeros(3)                                       # Newell normal of the exterior ring
        for p, q in zip(ex, np.roll(ex, -1, axis=0)):
            nrm += [(p[1] - q[1]) * (p[2] + q[2]), (p[2] - q[2]) * (p[0] + q[0]), (p[0] - q[0]) * (p[1] + q[1])]
        if not np.linalg.norm(nrm): continue                    # degenerate face
        k = int(np.argmax(np.abs(nrm)))
        uv = np.delete(a, k, axis=1)
        idx = earcut.triangulate_float64(uv, np.array(ring_ends, dtype=np.uint32)).reshape(-1, 3)
        base = len(V); V.extend(pts)
        for t in idx:
            tn = np.cross(a[t[1]] - a[t[0]], a[t[2]] - a[t[0]])
            if tn @ nrm < 0: t = t[::-1]
            F.append(base + t); O.append(owner)
    return np.array(V, float).reshape(-1, 3), np.array(F, int).reshape(-1, 3), np.array(O, int)


def write_tiles(V, F, owner, names, out, epsg):
    """Write tileset.json + buildings.glb into `out`. V: model-CRS vertices (absolute), F: triangles, owner: building index per triangle."""
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    assert len(names) == len(set(names)), "duplicate building ids"
    if not len(F): raise ValueError("no geometry to build")
    # model CRS -> ECEF -> local ENU at the centre (glTF is Y-up: (e, u, -n); 3D Tiles turns it back to Z-up)
    ecef = np.column_stack(Transformer.from_crs(epsg, 4978, always_xy=True).transform(*V.T))
    c = ecef.mean(0); lon, lat = np.radians(Transformer.from_crs(4978, 4326, always_xy=True).transform(*c)[:2])
    E = np.array([-np.sin(lon), np.cos(lon), 0])
    N = np.array([-np.sin(lat) * np.cos(lon), -np.sin(lat) * np.sin(lon), np.cos(lat)])
    U = np.array([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])
    d = ecef - c; enu = np.column_stack([d @ E, d @ N, d @ U])
    tri = enu[F]                                                # non-indexed -> flat per-face normals
    nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]); nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    gl = lambda a: np.stack([a[..., 0], a[..., 2], -a[..., 1]], -1).astype("<f4")
    pos, nor = gl(tri).reshape(-1, 3), np.repeat(gl(nrm), 3, axis=0)
    fid = np.repeat(owner, 3).astype("<f4")

    ids = [n.encode() for n in names]
    offs = np.cumsum([0] + [len(b) for b in ids]).astype("<u4")
    g = GLTF2(asset=Asset(version="2.0"), scene=0, scenes=[Scene(nodes=[0])], nodes=[Node(mesh=0)])
    blob, bvs = b"", {}
    for k, b in [("pos", pos.tobytes()), ("nor", nor.tobytes()), ("fid", fid.tobytes()), ("str", b"".join(ids)), ("off", offs.tobytes())]:
        b += b"\0" * (-len(b) % 4)
        g.bufferViews.append(BufferView(buffer=0, byteOffset=len(blob), byteLength=len(b), target=34962 if k in ("pos", "nor", "fid") else None))
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
    g.save_binary(str(out / "buildings.glb"))
    lo, hi = enu.min(0), enu.max(0); ctr, half = (lo + hi) / 2, (hi - lo) / 2
    (out / "tileset.json").write_text(json.dumps({
        "asset": {"version": "1.1"}, "geometricError": 500,
        "root": {"boundingVolume": {"box": [*ctr, half[0], 0, 0, 0, half[1], 0, 0, 0, half[2]]}, "geometricError": 0, "refine": "ADD",
                 "transform": [*E, 0, *N, 0, *U, 0, *c, 1], "content": {"uri": "buildings.glb"}}}))
