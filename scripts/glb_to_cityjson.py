#!/usr/bin/env python3
"""SketchUp GLB file(s) (one mesh node per roof-outline part) -> one CityJSON 2.0 (EPSG:32750) with ONE Building PER SITE (tapak).
Usage: scripts/glb_to_cityjson.py OUT.city.json [--ground GROUND.city.json | --z0 M] [--origin E,N] [--tol 1.0] [--epsg 32750] A.glb [B.glb ...]
Per GLB: nama = file name, the model origin (UTM easting northing) comes from koordinat.txt next to the GLB (or --origin for all GLBs).
Parts whose footprints touch or lie within --tol metres of each other become one Building: objectid LMK_<NAMA>_<nn> (biggest site = 01),
measuredHeight = tallest part, area = sum of part footprints, jumlahBagian = number of parts, geometry = one lod-1 MultiSolid
(tiles are built from lod1Solid only, see api/catalog/edit.py mesh_from_db).
Z: the model has no ground level, so its origin sits on the ground of the nearest building in GROUND.city.json (e.g. data/kipp/KIPP_LOD1.city.json), or on --z0 (default 0, with a warning).
Needs numpy, shapely, pygltflib. Upload the result with POST /imports (dataset=oikn_kipp theme=building lod=1)."""
import argparse, json, re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from pygltflib import GLTF2
from shapely import STRtree
from shapely.geometry import Polygon
from shapely.ops import unary_union

SCALE = 0.001
DTYPE = {5121: "u1", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def accessor(g, i):
    a = g.accessors[i]; bv = g.bufferViews[a.bufferView]; n = NCOMP[a.type]; dt = np.dtype(DTYPE[a.componentType])
    start = (bv.byteOffset or 0) + (a.byteOffset or 0); stride = bv.byteStride or n * dt.itemsize
    raw = np.frombuffer(g.binary_blob(), np.uint8, count=(a.count - 1) * stride + n * dt.itemsize, offset=start)
    return np.lib.stride_tricks.as_strided(raw, (a.count, n * dt.itemsize), (stride, 1)).copy().view(dt).reshape(a.count, n)


def local_matrix(n):
    if n.matrix: return np.array(n.matrix).reshape(4, 4).T
    x, y, z, w = n.rotation or [0, 0, 0, 1]
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    M = np.eye(4); M[:3, :3] = R * (n.scale or [1, 1, 1]); M[:3, 3] = n.translation or [0, 0, 0]
    return M


def read_parts(path):
    """One (vertices Nx3, triangles Tx3) per mesh node, in model metres, Z-up (glTF is Y-up: (x, y, z) -> (x, -z, y))."""
    g = GLTF2.load(str(path)); parts = []

    def walk(i, M):
        n = g.nodes[i]; M = M @ local_matrix(n)
        if n.mesh is not None:
            for p in g.meshes[n.mesh].primitives:
                if p.mode not in (None, 4): continue
                P = accessor(g, p.attributes.POSITION).astype(float)
                F = accessor(g, p.indices).reshape(-1, 3) if p.indices is not None else np.arange(len(P)).reshape(-1, 3)
                W = (np.c_[P, np.ones(len(P))] @ M.T)[:, :3]
                parts.append((np.c_[W[:, 0], -W[:, 2], W[:, 1]], F.astype(int)))
        for c in n.children: walk(c, M)

    for s in g.scenes[g.scene or 0].nodes: walk(s, np.eye(4))
    return parts


def weld(V, F):
    """Merge vertices closer than SCALE (an export repeats each vertex per face) and drop collapsed triangles."""
    u, inv = np.unique(np.round(V / SCALE).astype(np.int64), axis=0, return_inverse=True)
    F = inv.reshape(-1)[F]
    return u * SCALE, F[(F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 0] != F[:, 2])]


def orient(V, F):
    """Consistent, outward-facing triangles. The export mixes windings (some faces are mirrored by a negative node scale), so first flip faces
    until every shared edge is traversed in opposite directions, then flip all if the signed volume is negative.
    Call with model-local coordinates; UTM-sized numbers lose the precision."""
    F = F.copy(); edges = defaultdict(list)
    for i, f in enumerate(F.tolist()):
        for e in zip(f, f[1:] + f[:1]): edges[frozenset(e)].append(i)
    seen = np.zeros(len(F), bool)
    for start in range(len(F)):
        if seen[start]: continue
        seen[start] = True; todo = [start]
        while todo:
            i = todo.pop(); f = F[i].tolist()
            for e in zip(f, f[1:] + f[:1]):
                for j in edges[frozenset(e)]:
                    if seen[j]: continue
                    g = F[j].tolist()
                    if e in zip(g, g[1:] + g[:1]): F[j] = F[j][::-1]       # same direction as us -> flip
                    seen[j] = True; todo.append(j)
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    return F if np.einsum("ij,ij->", a, np.cross(b, c)) >= 0 else F[:, ::-1]


def footprint(V, F):
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    nz = np.cross(b - a, c - a)[:, 2]; nz = nz / np.maximum(np.linalg.norm(np.cross(b - a, c - a), axis=1), 1e-12)
    tris = [Polygon(V[f][:, :2]) for f, k in zip(F, nz) if k < -0.5]       # downward faces = the footprint of an extruded outline
    return unary_union([t for t in tris if t.area > 1e-9]).buffer(0)


def closed(F):
    e = Counter(tuple(sorted(p)) for f in F.tolist() for p in zip(f, f[1:] + f[:1]))
    return all(c == 2 for c in e.values())


def clusters(parts, tol):
    P = [p["fp"].buffer(tol / 2) for p in parts]; t = STRtree(P); par = list(range(len(P)))

    def find(x):
        while par[x] != x: par[x] = par[par[x]]; x = par[x]
        return x
    for i, p in enumerate(P):
        for j in t.query(p):
            if j > i and p.intersects(P[j]): par[find(i)] = find(j)
    out = defaultdict(list)
    for i, p in enumerate(parts): out[find(i)].append(p)
    return sorted(out.values(), key=lambda c: -sum(p["fp"].area for p in c))


def ground_index(path):
    """(x, y, ground z) per object of a CityJSON: centre of its extent and its lowest vertex."""
    d = json.load(open(path)); V = np.array(d["vertices"]) * d["transform"]["scale"] + d["transform"]["translate"]
    def ids(x): return {x} if isinstance(x, int) else set().union(*map(ids, x)) if x else set()
    pts = []
    for o in d["CityObjects"].values():
        v = V[sorted(set().union(*[ids(g["boundaries"]) for g in o.get("geometry", [])]))]
        if len(v): pts.append(((v[:, 0].min() + v[:, 0].max()) / 2, (v[:, 1].min() + v[:, 1].max()) / 2, v[:, 2].min()))
    return np.array(pts)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out"); ap.add_argument("glb", nargs="+")
    ap.add_argument("--ground"); ap.add_argument("--z0", type=float, default=0.0); ap.add_argument("--origin")
    ap.add_argument("--tol", type=float, default=1.0); ap.add_argument("--epsg", type=int, default=32750)
    a = ap.parse_args()
    ground = ground_index(a.ground) if a.ground else None
    if ground is None: print(f"warning: no --ground, every model origin sits at z = {a.z0}")
    verts, vid, objs = [], {}, {}

    def vert(xyz):
        k = tuple(int(round(c / SCALE)) for c in xyz)
        if k not in vid: vid[k] = len(verts); verts.append(k)
        return vid[k]

    for path in map(Path, a.glb):
        nama = path.stem.strip(); slug = re.sub(r"\W+", "_", nama).strip("_").upper()
        if a.origin: ox, oy = map(float, a.origin.split(","))
        else: ox, oy = map(float, next(path.parent.glob("[kK]oordinat.txt")).read_text().split())
        z0 = float(ground[np.argmin(np.hypot(ground[:, 0] - ox, ground[:, 1] - oy))][2]) if ground is not None else a.z0
        parts = []
        for V, F in read_parts(path):
            V, F = weld(V, F); F = orient(V, F); V = V + [ox, oy, z0]
            parts.append({"V": V, "F": F, "fp": footprint(V, F), "h": float(V[:, 2].max() - V[:, 2].min()), "closed": closed(F)})
        for n, c in enumerate(clusters(parts, a.tol), 1):
            solids = [[[[[vert(p["V"][j]) for j in f]] for f in p["F"]]] for p in c]        # MultiSolid: solids -> shells -> faces -> rings
            objs[f"LMK_{slug}_{n:02d}"] = {"type": "Building", "attributes": {
                "nama": nama, "measuredHeight": round(max(p["h"] for p in c), 2), "area": round(sum(p["fp"].area for p in c), 2), "jumlahBagian": len(c)},
                "geometry": [{"type": "MultiSolid", "lod": "1", "boundaries": solids}]}
        sites = sum(k.startswith(f"LMK_{slug}_") for k in objs)
        print(f"{nama}: {len(parts)} parts -> {sites} buildings, {sum(not p['closed'] for p in parts)} parts not closed, origin ({ox:.1f}, {oy:.1f}), ground z {z0:.2f}")
    arr = np.array(verts) * SCALE; t0 = np.floor(arr.min(0))
    doc = {"type": "CityJSON", "version": "2.0", "transform": {"scale": [SCALE] * 3, "translate": t0.tolist()},
           "metadata": {"referenceSystem": f"http://www.opengis.net/def/crs/EPSG/0/{a.epsg}",
                        "geographicalExtent": [*arr.min(0).round(3).tolist(), *arr.max(0).round(3).tolist()]},
           "CityObjects": objs, "vertices": np.round((arr - t0) / SCALE).astype(int).tolist()}
    Path(a.out).write_text(json.dumps(doc, separators=(",", ":")))
    print(f"{len(objs)} buildings, {len(verts)} vertices -> {a.out}")


if __name__ == "__main__":
    main()
