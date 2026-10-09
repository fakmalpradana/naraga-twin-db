"""Write path of the API: create / update / delete LOD1 buildings and keep the served tileset in sync.

How a write works (one request = one serialized operation):
  1. Build a one-building CityGML 2.0 file (block model: footprint extruded to a height).
  2. Write it to 3DCityDB with citydb-tool (the same importer as scripts/import.sh), so the stored structure is identical to imported data.
  3. Rebuild the layer's tiles from the database, register them as the next tileset version and make it the active one.
  4. Record an import_job row (who, what, when).
Callers must hold a write token (see require_writer). Reads never go through this module except read_building/mesh_from_db."""
import hashlib, json, os, re, shutil, subprocess, tempfile, uuid
from pathlib import Path
from urllib.parse import urlparse
from xml.sax.saxutils import escape, quoteattr

from django.conf import settings
from django.db import connections, transaction
from ninja.errors import HttpError
from pyproj import Transformer
from shapely.geometry import Polygon

from . import tiles

LOCK_KEY = 738291                       # advisory lock: one write at a time
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
KEEP_VERSIONS = 2                       # tile folders kept on disk per layer (newest first)


# ---------------------------------------------------------------- auth
def require_writer(request):
    """Bearer token + X-User header. Returns the user name that is stamped on every change."""
    token = getattr(settings, "WRITE_API_TOKEN", "")
    if not token:
        raise HttpError(503, "Write API is disabled (WRITE_API_TOKEN is not set).")
    given = request.headers.get("Authorization", "")
    if not given.startswith("Bearer ") or not secrets_equal(given[7:], token):
        raise HttpError(401, "Missing or invalid bearer token.")
    user = request.headers.get("X-User", "").strip()
    if not re.fullmatch(r"[\w .@-]{1,64}", user):
        raise HttpError(400, "Header X-User (who is making the change, max 64 chars) is required.")
    return user


def secrets_equal(a, b):
    import hmac
    return hmac.compare_digest(a.encode(), b.encode())


# ---------------------------------------------------------------- reading
def _rows(sql, params=(), alias="ro"):
    with connections[alias].cursor() as cur:
        cur.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def db_srid():
    return _rows("SELECT srid FROM citydb.database_srs LIMIT 1")[0]["srid"]


def parse_lineage(lineage):
    ds, theme, lod = (lineage or "").split(".")
    return ds, theme, int(lod.removeprefix("lod"))


def feature_row(objectid):
    r = _rows("SELECT id, lineage FROM citydb.feature WHERE objectid = %s AND termination_date IS NULL AND lineage IS NOT NULL ORDER BY id LIMIT 1", [objectid])
    return r[0] if r else None


def solid_faces(feature_id):
    """Polygons (rings of x,y,z) of the building's lod1Solid."""
    rows = _rows("""SELECT ST_AsGeoJSON(d.geom, 3) AS g FROM citydb.property p
                    JOIN citydb.geometry_data gd ON gd.id = p.val_geometry_id
                    CROSS JOIN LATERAL (SELECT (ST_Dump(gd.geometry)).geom) d
                    WHERE p.feature_id = %s AND p.name = 'lod1Solid'""", [feature_id])
    return [json.loads(r["g"])["coordinates"] for r in rows]


def read_building(objectid):
    """Current state of a building as the write API understands it, or None if it does not exist.
    `simple` is False when the ground is not exactly one polygon (such objects cannot be edited by PATCH)."""
    f = feature_row(objectid)
    if not f: return None
    faces = solid_faces(f["id"])
    attrs = {r["name"]: r["val_string"] if r["val_string"] is not None else r["val_double"]
             for r in _rows("""SELECT name, val_string, val_double FROM citydb.property
                               WHERE feature_id = %s AND parent_id IS NULL AND namespace_id = 3 AND datatype_id IN (4, 5)""", [f["id"]])}
    out = {"objectid": objectid, "lineage": f["lineage"], "attributes": attrs, "simple": False, "rings": None, "base_z": None, "height_m": None}
    if faces:
        zs = [p[2] for face in faces for ring in face for p in ring]
        zmin, zmax = min(zs), max(zs)
        ground = [fc for fc in faces if all(abs(p[2] - zmin) < 1e-6 for ring in fc for p in ring)]
        out.update(base_z=round(zmin, 3), height_m=round(zmax - zmin, 3))
        if len(ground) == 1:
            out.update(simple=True, rings=[[(p[0], p[1]) for p in ring] for ring in ground[0]])
    return out


def mean_ground_z(lineage):
    r = _rows("""SELECT avg(ST_ZMin(gd.geometry)) AS z FROM citydb.feature f
                 JOIN citydb.property p ON p.feature_id = f.id AND p.name = 'lod1Solid'
                 JOIN citydb.geometry_data gd ON gd.id = p.val_geometry_id
                 WHERE f.lineage = %s AND f.termination_date IS NULL""", [lineage])
    return round(float(r[0]["z"] or 0), 2)


def layer_row(dataset, theme, lod, alias="ro"):
    r = _rows("""SELECT l.id AS layer_id, d.id AS dataset_id, l.status FROM catalog.layer l JOIN catalog.dataset d ON d.id = l.dataset_id
                 WHERE d.code = %s AND l.theme_code = %s AND l.lod = %s""", [dataset, theme, lod], alias)
    if not r: raise HttpError(404, f"Layer {dataset}/{theme}/LOD{lod} does not exist.")
    return r[0]


def mesh_from_db(lineage):
    rows = _rows("""SELECT f.objectid, ST_AsGeoJSON(d.geom, 3) AS g FROM citydb.feature f
                    JOIN citydb.property p ON p.feature_id = f.id AND p.name = 'lod1Solid'
                    JOIN citydb.geometry_data gd ON gd.id = p.val_geometry_id
                    CROSS JOIN LATERAL (SELECT (ST_Dump(gd.geometry)).geom) d
                    WHERE f.lineage = %s AND f.termination_date IS NULL ORDER BY f.id""", [lineage])
    names, index, faces = [], {}, []
    for r in rows:
        if r["objectid"] not in index: index[r["objectid"]] = len(names); names.append(r["objectid"])
        faces.append((index[r["objectid"]], json.loads(r["g"])["coordinates"]))
    V, F, owner = tiles.triangulate(faces)
    return names, V, F, owner


# ---------------------------------------------------------------- building -> CityGML
def _area(ring):
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1])) / 2


def _open(ring):
    return ring[:-1] if ring[0] == ring[-1] else list(ring)


def validate_rings(rings_xy):
    ext, holes = rings_xy[0], rings_xy[1:]
    if len(_open(ext)) < 3: raise HttpError(422, "footprint needs at least 3 distinct points")
    poly = Polygon(_open(ext), [_open(h) for h in holes])
    if not poly.is_valid: raise HttpError(422, "footprint is not a valid polygon (self-intersection or holes outside)")
    if poly.area < 1: raise HttpError(422, "footprint area is below 1 square metre")


def building_gml(objectid, attrs, rings_xy, base_z, height, srid):
    """CityGML 2.0 for one block-model building. rings_xy = [exterior, *holes] in the model CRS."""
    validate_rings(rings_xy)
    rings = []
    for i, r in enumerate(rings_xy):                      # exterior counter-clockwise, holes clockwise (seen from above)
        r = _open(r); ccw = _area(r) > 0
        rings.append(r if ccw == (i == 0) else r[::-1])
    z0, z1 = base_z, base_z + height
    poly = lambda rs: ("<gml:surfaceMember><gml:Polygon>"
                       + "".join(f"<gml:{'exterior' if i == 0 else 'interior'}><gml:LinearRing><gml:posList>"
                                 + " ".join(f"{x:.3f} {y:.3f} {z:.3f}" for x, y, z in r + [r[0]])
                                 + f"</gml:posList></gml:LinearRing></gml:{'exterior' if i == 0 else 'interior'}>" for i, r in enumerate(rs))
                       + "</gml:Polygon></gml:surfaceMember>")
    at = lambda r, z: [(x, y, z) for x, y in r]
    faces = [poly([at(r, z0)[::-1] for r in rings]), poly([at(r, z1) for r in rings])]   # ground (facing down), roof
    for r in rings:
        for (x1, y1), (x2, y2) in zip(r, r[1:] + r[:1]):
            faces.append(poly([[(x1, y1, z0), (x2, y2, z0), (x2, y2, z1), (x1, y1, z1)]]))
    gen = ""
    for k, v in attrs.items():
        if not NAME_RE.fullmatch(k): raise HttpError(422, f"attribute name '{k}' is not allowed (letters, digits, underscore)")
        if isinstance(v, bool) or isinstance(v, str):
            gen += f"<gen:stringAttribute name={quoteattr(k)}><gen:value>{escape(str(v))}</gen:value></gen:stringAttribute>"
        else:
            gen += f"<gen:doubleAttribute name={quoteattr(k)}><gen:value>{float(v)}</gen:value></gen:doubleAttribute>"
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
            'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" xmlns:gen="http://www.opengis.net/citygml/generics/2.0" '
            'xmlns:gml="http://www.opengis.net/gml"><core:cityObjectMember>'
            f'<bldg:Building gml:id={quoteattr(objectid)}>{gen}<bldg:measuredHeight uom="m">{height}</bldg:measuredHeight>'
            f'<bldg:lod1Solid><gml:Solid srsName="urn:ogc:def:crs:EPSG::{srid}" srsDimension="3"><gml:exterior><gml:CompositeSurface>'
            f'{"".join(faces)}</gml:CompositeSurface></gml:exterior></gml:Solid></bldg:lod1Solid></bldg:Building></core:cityObjectMember></core:CityModel>')


def lonlat_to_model(rings_ll, srid):
    tr = Transformer.from_crs(4326, srid, always_xy=True)
    out = []
    for r in rings_ll:
        if len(r) < 3 or any(len(p) != 2 for p in r): raise HttpError(422, "footprint must be a list of [lon, lat] points")
        if any(not (-180 <= p[0] <= 180 and -90 <= p[1] <= 90) for p in r): raise HttpError(422, "footprint coordinates must be lon/lat (EPSG:4326)")
        out.append([tr.transform(*p) for p in r])
    return out


def model_to_lonlat(rings, srid):
    tr = Transformer.from_crs(srid, 4326, always_xy=True)
    return [[list(tr.transform(*p)) for p in r] for r in rings]


# ---------------------------------------------------------------- citydb-tool
def _citydb(sub, options, timeout=180):
    u = urlparse(os.environ["DATABASE_URL"])
    conn = ["-H", u.hostname, "-P", str(u.port or 5432), "-d", (u.path or "/postgres").lstrip("/"), "-u", u.username, "-p", u.password or ""]
    cmd = [settings.CITYDB_TOOL, *sub, *conn, *options]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError("citydb-tool failed: " + (r.stderr or r.stdout)[-600:].replace(u.password or "\0", "***"))
    return r.stdout


def import_gml(gml, lineage, user, reason):
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "b.gml"; f.write_text(gml)
        _citydb(["import", "citygml"], ["--lineage", lineage, "--reason-for-update", reason, "--updating-person", user,
                                        "-m", "import_all", "--compute-extent", str(f)])


def delete_ids(objectid, lineage):
    assert ID_RE.fullmatch(objectid) and re.fullmatch(r"[a-z0-9_.]+", lineage)   # both go into a CQL filter
    _citydb(["delete"], ["--delete-mode=delete", "-f", f"objectid = '{objectid}' AND lineage = '{lineage}'"])


# ---------------------------------------------------------------- catalog bookkeeping + tile sync
def record_job(cur, layer, lineage, mode, source, payload, counts):
    sha = hashlib.sha256(payload.encode()).hexdigest()
    cur.execute("""INSERT INTO catalog.import_job (dataset_id, source_file, source_sha256, source_size_bytes, import_mode, lineage_tag,
                     tool_version, citygml_version, status, counts, finished_at) VALUES (%s,%s,%s,%s,%s,%s,'1.4.0','2.0','succeeded',%s::jsonb, now())
                   RETURNING id""", [layer["dataset_id"], source, sha, len(payload), mode, lineage, json.dumps(counts)])
    job = cur.fetchone()[0]
    cur.execute("INSERT INTO catalog.import_job_layer VALUES (%s, %s)", [job, layer["layer_id"]])
    return job


def sync_tiles(cur, layer, dataset, theme, lod):
    """Rebuild tiles from the database and make them the active tileset of the layer."""
    lineage = f"{dataset}.{theme}.lod{lod}"
    names, V, F, owner = mesh_from_db(lineage)
    if not names: raise HttpError(409, "A layer must keep at least one building.")
    cur.execute("SELECT coalesce(max(version), 0) + 1 FROM catalog.tileset WHERE layer_id = %s", [layer["layer_id"]])
    version = cur.fetchone()[0]
    rel = f"tiles/{dataset}/{theme}/lod{lod}"
    out = Path(settings.ROOT) / rel / f"v{version}"
    tiles.write_tiles(V, F, owner, names, out, db_srid())
    try:
        cur.execute("""INSERT INTO catalog.tileset (layer_id, version, provider, url, converter_name, converter_version, source_snapshot_at,
                         height_offset_m, status, is_active)
                       SELECT %s, %s, 'self_hosted', %s, 'api/catalog/tiles.py', '1', clock_timestamp(),
                              coalesce((SELECT height_offset_m FROM catalog.tileset WHERE layer_id = %s AND is_active), 0), 'ready', false""",
                    [layer["layer_id"], version, f"/{rel}/v{version}/tileset.json", layer["layer_id"]])
        cur.execute("UPDATE catalog.tileset SET is_active = false, status = 'retired' WHERE layer_id = %s AND is_active", [layer["layer_id"]])
        cur.execute("UPDATE catalog.tileset SET is_active = true WHERE layer_id = %s AND version = %s", [layer["layer_id"], version])
    except Exception:
        shutil.rmtree(out, ignore_errors=True); raise
    transaction.on_commit(lambda: _prune(Path(settings.ROOT) / rel))
    return version, f"/{rel}/v{version}/tileset.json", len(names)


def _prune(base):
    vs = sorted((p for p in base.glob("v*") if p.name[1:].isdigit()), key=lambda p: int(p.name[1:]), reverse=True)
    for p in vs[KEEP_VERSIONS:]: shutil.rmtree(p, ignore_errors=True)


def operation(user, dataset, theme, lod, work, rebuild=True):
    """Run `work(cur)` (the citydb change) under the global write lock, then catalog bookkeeping and tile sync."""
    layer = layer_row(dataset, theme, lod)
    with transaction.atomic(using="default"):
        with connections["default"].cursor() as cur:
            cur.execute("SELECT set_config('app.user', %s, true)", [f"api:{user}"])
            cur.execute("SELECT pg_advisory_xact_lock(%s)", [LOCK_KEY])
            result = work(cur, layer)
            job = record_job(cur, layer, f"{dataset}.{theme}.lod{lod}", *result["job"])
            cur.execute("SELECT catalog.refresh_derived()")
            tile = None
            if rebuild:
                try:
                    v, url, n = sync_tiles(cur, layer, dataset, theme, lod)
                except HttpError:
                    raise
                except Exception as e:
                    raise HttpError(502, "The data was saved but the tiles could not be rebuilt; call POST /layers/{id}/rebuild. " + str(e)[:200])
                tile = {"version": v, "url": url, "buildings": n}
    return {"objectid": result["objectid"], "action": result["action"], "jobId": str(job), "layerId": str(layer["layer_id"]),
            "tileset": tile, "stale": not rebuild}
