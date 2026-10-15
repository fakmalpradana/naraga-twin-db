"""data/LOD1_landmark/<NAMA>/LAS_lod1.geojson -> one GeoJSON ready for POST /imports.
Per feature: objectid LMK_<NAMA>_<n>, nama = folder name, height_m = BHM, base_z = ground of the nearest KIPP building
(from the oikn GeoJSON export), other properties kept as attributes. Folders without LAS_lod1.geojson are listed and skipped.
Usage: python3 scripts/prep_landmark.py data/LOD1_landmark oikn.geojson oikn_kipp.geojson"""
import json, re, sys
from pathlib import Path


def centroid(geom):
    ring = geom["coordinates"][0] if geom["type"] == "Polygon" else geom["coordinates"][0][0]
    return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)


def main(src, ground_file, out):
    kipp = [(centroid(f["geometry"]), f["properties"]["base_z"]) for f in json.load(open(ground_file))["features"] if f.get("geometry")]
    feats, no_height = [], []
    for d in sorted(Path(src).iterdir()):
        g = d / "LAS_lod1.geojson"
        if not g.exists():
            print(f"skip (no LAS_lod1.geojson): {d.name}"); continue
        slug = re.sub(r"[^A-Za-z0-9]+", "_", d.name).strip("_").upper()
        for i, f in enumerate(json.load(open(g))["features"], 1):
            p = dict(f["properties"])
            if p.get("BHM") is None:
                no_height.append(f"{d.name} #{i}"); continue
            c = centroid(f["geometry"])
            # ponytail: nearest KIPP centroid, O(n*m) and flat-earth distance; fine for ~2.4k x 389 inside one district
            ground = min(kipp, key=lambda k: (k[0][0] - c[0]) ** 2 + (k[0][1] - c[1]) ** 2)[1]
            p.update(objectid=f"LMK_{slug}_{i:04d}", nama=d.name, height_m=round(float(p.pop("BHM")), 2), base_z=ground)
            feats.append({"type": "Feature", "geometry": f["geometry"], "properties": p})
    if no_height: print(f"skip {len(no_height)} without BHM: {', '.join(no_height)}")
    bad = [f["properties"]["objectid"] for f in feats if not 0 < f["properties"]["height_m"] <= 1000]
    print(f"{len(feats)} buildings -> {out}" + (f"; {len(bad)} with height outside 0-1000 m will be skipped by the API" if bad else ""))
    Path(out).write_text(json.dumps({"type": "FeatureCollection", "features": feats}))


if __name__ == "__main__":
    main(*sys.argv[1:4])
