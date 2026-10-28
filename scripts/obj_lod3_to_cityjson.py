#!/usr/bin/env python3
"""One georeferenced LoD3 model (.obj from Autodesk ATF: local metres, Z-up, one `g` per part, `usemtl` per face run; or .glb, one primitive per material)
-> CityJSON 2.0 (EPSG:32750) with ONE Building.
Usage: scripts/obj_lod3_to_cityjson.py IN.obj|IN.glb OUT.city.json "NAMA GEDUNG" --origin E,N[,Z] [--ratio 1] [--min-faces 12]
Geometry = one lod-3 MultiSurface (parts are not guaranteed closed). Windows (glass materials) and doors get WindowSurface / DoorSurface semantics.
--ratio < 1 quadric-decimates each (group, material) chunk to max(ratio * faces, min-faces) triangles (default 1 = keep the model as is).
Needs numpy and fast-simplification (+ pygltflib for .glb). Upload with POST /imports (dataset=oikn_kipp theme=building lod=3)."""
import argparse, json, re
from collections import defaultdict
from pathlib import Path

import fast_simplification as fs
import numpy as np

SCALE = 0.001
SURF = ["WindowSurface", "DoorSurface"]


def surface_of(group, mat):
    if re.search(r"glass|window", mat, re.I): return 0
    if re.search(r"door", mat + " " + group, re.I): return 1
    return None


def read_obj(path):
    """-> V (Nx3), chunks [(group, material, faces Fx3 of 0-based vertex ids)]."""
    V, chunks, group, mat = [], defaultdict(list), "", ""
    with open(path) as f:
        for line in f:
            if line.startswith("v "): V.append(line.split()[1:4])
            elif line.startswith("f "):
                chunks[(group, mat)].append([int(t.split("/")[0]) - 1 for t in line.split()[1:4]])
            elif line.startswith("g "): group = line[2:].strip()
            elif line.startswith("usemtl"): mat = line.split()[1]
    return np.array(V, float), {k: np.array(v, np.int32) for k, v in chunks.items()}


def read_glb(path):
    """Same as read_obj for a .glb (Y-up -> Z-up); one chunk per (node, material) primitive."""
    import sys; sys.path.insert(0, str(Path(__file__).parent))
    import glb_to_cityjson as G
    from pygltflib import GLTF2
    g = GLTF2.load(str(path)); V, chunks = [], {}

    def walk(i, M):
        n = g.nodes[i]; M = M @ G.local_matrix(n)
        for p in (g.meshes[n.mesh].primitives if n.mesh is not None else []):
            if p.mode not in (None, 4): continue
            P = G.accessor(g, p.attributes.POSITION).astype(float)
            F = G.accessor(g, p.indices).reshape(-1, 3) if p.indices is not None else np.arange(len(P)).reshape(-1, 3)
            W = (np.c_[P, np.ones(len(P))] @ M.T)[:, :3]
            base = sum(map(len, V)); V.append(np.c_[W[:, 0], -W[:, 2], W[:, 1]])
            chunks[(n.name or "", g.materials[p.material].name if p.material is not None else "")] = F.astype(np.int32) + base
        for c in n.children: walk(c, M)

    for s in g.scenes[g.scene or 0].nodes: walk(s, np.eye(4))
    return np.vstack(V), chunks


def decimate(V, F, ratio, min_faces):
    target = max(int(len(F) * ratio), min_faces)
    if len(F) <= target: return V, F
    used, inv = np.unique(F, return_inverse=True)
    v, f = fs.simplify(V[used].astype(np.float32), inv.reshape(-1, 3).astype(np.int32), target_count=target, agg=5)
    return v.astype(float), f


def main(obj, out, name, origin, ratio, min_faces):
    O = np.array(origin + [0.0] * (3 - len(origin)))
    V, chunks = (read_glb if str(obj).lower().endswith('.glb') else read_obj)(Path(obj))
    print(f"{len(V)} vertices, {sum(map(len, chunks.values()))} triangles, {len(chunks)} (group, material) chunks")
    vid, verts, faces, surf = {}, [], [], []
    for (group, mat), F in chunks.items():
        v, f = decimate(V, F, ratio, min_faces)
        w = np.round((v + O) / SCALE).astype(np.int64)                    # absolute, 1 mm
        ids = []
        for k in map(tuple, w.tolist()):
            if k not in vid: vid[k] = len(verts); verts.append(k)
            ids.append(vid[k])
        ids = np.array(ids)
        for t in ids[f]:
            if len({*t}) == 3: faces.append([[int(i) for i in t]]); surf.append(surface_of(group, mat))
    a = np.array(verts) * SCALE
    t0 = np.floor(a.min(0))
    iv = (np.array(verts) - (t0 / SCALE).astype(np.int64)).tolist()
    used = sorted({s for s in surf if s is not None})
    geom = {"type": "MultiSurface", "lod": "3", "boundaries": faces,
            "semantics": {"surfaces": [{"type": SURF[i]} for i in used], "values": [used.index(s) if s is not None else None for s in surf]}}
    slug = re.sub(r"\W+", "_", name.strip()).strip("_").upper()
    doc = {"type": "CityJSON", "version": "2.0", "transform": {"scale": [SCALE] * 3, "translate": t0.tolist()},
           "metadata": {"referenceSystem": "http://www.opengis.net/def/crs/EPSG/0/32750",
                        "geographicalExtent": [*a.min(0).round(3).tolist(), *a.max(0).round(3).tolist()]},
           "CityObjects": {f"LMK_{slug}_0001": {"type": "Building", "geometry": [geom],
                           "attributes": {"nama": name, "measuredHeight": round(float(a[:, 2].max() - a[:, 2].min()), 2)}}},
           "vertices": iv}
    Path(out).write_text(json.dumps(doc, separators=(",", ":")))
    print(f"{len(faces)} triangles, {len(verts)} vertices, extent {doc['metadata']['geographicalExtent']} -> {out}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("obj"); p.add_argument("out"); p.add_argument("name")
    p.add_argument("--origin", required=True, type=lambda s: [float(x) for x in s.split(",")], help="E,N[,Z] of the model origin, UTM 50S")
    p.add_argument("--ratio", type=float, default=1.0); p.add_argument("--min-faces", type=int, default=12)
    a = p.parse_args()
    main(a.obj, a.out, a.name, a.origin, a.ratio, a.min_faces)
