#!/usr/bin/env python3
"""End-to-end CRUD + sync check against a running API. Creates one test building, reads it, updates it, deletes it, and after
every step compares the database (API) with the tiles that the frontend would load (ids inside buildings.glb).
Usage: scripts/crud_smoke.py [BASE_URL=http://localhost:8000]   (token is read from WRITE_API_TOKEN or .env)
Leaves the layer as it was (the test building is deleted), but tileset versions advance."""
import json, os, struct, sys, urllib.request, urllib.error
from pathlib import Path

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
TOKEN = os.environ.get("WRITE_API_TOKEN") or next((l.split("=", 1)[1].split("#")[0].strip() for l in Path(".env").read_text().splitlines() if l.startswith("WRITE_API_TOKEN=")), "")
DATASET, THEME = "oikn", "building"


def call(method, path, body=None, auth=True, user="smoke-test"):
    h = {"Content-Type": "application/json"}
    if auth: h["Authorization"] = f"Bearer {TOKEN}"
    if user: h["X-User"] = user
    req = urllib.request.Request(BASE + path, method=method, headers=h, data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=300) as r: return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")


def tile_ids():
    """(version, ids) of the active tileset exactly as the frontend resolves it: /catalog -> tileset.json -> glb -> property table."""
    _, cat = call("GET", f"/api/v1/catalog?dataset={DATASET}&theme={THEME}&lod=1", auth=False)
    ts = cat["items"][0]["tileset"]
    base = ts["url"].rsplit("/", 1)[0]
    uri = json.load(urllib.request.urlopen(ts["url"]))["root"]["content"]["uri"]
    glb = urllib.request.urlopen(f"{base}/{uri}").read()
    jlen = struct.unpack("<I", glb[12:16])[0]; js = json.loads(glb[20:20 + jlen]); binc = glb[20 + jlen + 8:]
    pt = js["extensions"]["EXT_structural_metadata"]["propertyTables"][0]["properties"]["objectid"]
    bv = lambda i: binc[js["bufferViews"][i].get("byteOffset", 0):][:js["bufferViews"][i]["byteLength"]]
    offs = struct.unpack(f"<{len(bv(pt['stringOffsets'])) // 4}I", bv(pt["stringOffsets"])); vals = bv(pt["values"])
    return ts["version"], {vals[a:b].decode() for a, b in zip(offs, offs[1:])}


def stale():
    return call("GET", f"/api/v1/catalog?dataset={DATASET}&theme={THEME}&lod=1", auth=False)[1]["items"][0]["isStale"]


def db_count():
    _, s = call("GET", f"/api/v1/datasets/{DATASET}/stats", auth=False)
    return next(i["featureCount"] for i in s["items"] if i["class"] == "Building" and i["lod"] == "1")


fails = []
def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else "")); ok or fails.append(name)


v0, ids0 = tile_ids(); n0 = db_count()
check("baseline: database count equals buildings in tiles", n0 == len(ids0), f"db={n0} tiles={len(ids0)}")

# --- auth
check("no token -> 401", call("POST", "/api/v1/features", {}, auth=False)[0] == 401)
check("token without X-User -> 400", call("POST", "/api/v1/features", {"dataset": DATASET, "footprint": [[0, 0]], "height_m": 1}, user=None)[0] == 400)

# --- CREATE
ring = [[116.7090, -0.9720], [116.7094, -0.9720], [116.7094, -0.9717], [116.7090, -0.9717]]
st, r = call("POST", "/api/v1/features", {"dataset": DATASET, "footprint": ring, "height_m": 25, "attributes": {"nama": "CRUD TEST", "lantai": 6}})
check("create -> 201", st == 201, str(r)[:300]); oid = r.get("objectid")
check("create: new tileset version", r.get("tileset", {}).get("version", 0) > v0, str(r.get("tileset")))
check("create: database count +1", db_count() == n0 + 1)
check("create: layer not stale", stale() is False)
v1, ids1 = tile_ids()
check("create: tiles contain the new building (sync)", oid in ids1 and len(ids1) == n0 + 1 and v1 > v0, f"v={v1} n={len(ids1)}")
check("create: duplicate objectid -> 409", call("POST", "/api/v1/features", {"dataset": DATASET, "objectid": oid, "footprint": ring, "height_m": 5})[0] == 409)
check("create: invalid footprint -> 422", call("POST", "/api/v1/features", {"dataset": DATASET, "footprint": [[116.7, -0.97], [116.71, -0.97]], "height_m": 5})[0] == 422)

# --- READ
st, g = call("GET", f"/api/v1/features/{oid}", auth=False)
attrs = {a["name"]: a["value"] for a in g.get("attributes", [])}
check("read: attributes stored", st == 200 and attrs.get("nama") == "CRUD TEST" and attrs.get("lantai") == 6.0, str(attrs))
check("read: height from database geometry", abs(g["geometry"]["heightM"] - 25) < 0.01, str(g.get("geometry"))[:200])
fp = g["geometry"]["footprint"][0]
check("read: footprint round-trips (lon/lat within 1e-6)", all(abs(a - b) < 1e-6 for p in ring for a, b in [min(((p[0], q[0]) for q in fp), key=lambda t: abs(t[0] - t[1]))]) and len(fp) >= 4)

# --- UPDATE
st, r = call("PATCH", f"/api/v1/features/{oid}", {"height_m": 40, "attributes": {"nama": "CRUD TEST EDITED", "lantai": None, "catatan": "diubah"}})
check("update -> 200", st == 200, str(r)[:300])
st, g = call("GET", f"/api/v1/features/{oid}", auth=False)
attrs = {a["name"]: a["value"] for a in g.get("attributes", [])}
check("update: new height in database", abs(g["geometry"]["heightM"] - 40) < 0.01)
check("update: attributes merged (edit, remove, add)", attrs.get("nama") == "CRUD TEST EDITED" and "lantai" not in attrs and attrs.get("catatan") == "diubah", str(attrs))
v2, ids2 = tile_ids()
check("update: tiles rebuilt and still consistent", v2 > v1 and oid in ids2 and len(ids2) == n0 + 1 and db_count() == n0 + 1, f"v={v2}")
check("update: layer not stale", stale() is False)
check("update: unknown object -> 404", call("PATCH", "/api/v1/features/NOPE_123", {"height_m": 3})[0] == 404)

# --- DELETE
st, r = call("DELETE", f"/api/v1/features/{oid}")
check("delete -> 200", st == 200, str(r)[:300])
check("delete: object gone (404)", call("GET", f"/api/v1/features/{oid}", auth=False)[0] == 404)
v3, ids3 = tile_ids()
check("delete: tiles back to original set", v3 > v2 and ids3 == ids0 and db_count() == n0, f"v={v3} n={len(ids3)}")
_, cat = call("GET", f"/api/v1/catalog?dataset={DATASET}&theme={THEME}&lod=1", auth=False)
check("layer not stale, count matches", cat["items"][0]["isStale"] is False and cat["items"][0]["featureCount"] == n0, str(cat["items"][0]["isStale"]))
print("\nALL PASSED" if not fails else f"\nFAILED: {fails}"); sys.exit(1 if fails else 0)
