"""API /api/v1 (django-ninja). Reads use the SELECT-only DB alias `ro`. Writes (POST/PATCH/DELETE on /features, POST /layers/{id}/rebuild)
are in catalog/edit.py, need a bearer token and keep the served tileset in sync."""
import json, uuid
from typing import Optional

from django.db import connections
from ninja import File, Form, NinjaAPI, Query, Schema, UploadedFile
from ninja.errors import HttpError, ValidationError

DESCRIPTION = """Layers and 3D Tiles for a Cesium (or any 3D Tiles 1.1) frontend, city object attributes from **3DCityDB**,
and, with a write token, create/update/delete of LOD1 buildings with automatic tile rebuild.

**Frontend in three calls**
1. `GET /api/v1/catalog?dataset=oikn&theme=building&lod=1` → `items[0].tileset.url` (absolute URL; the version changes after every edit, never hard-code it)
2. `Cesium.Cesium3DTileset.fromUrl(url)` → every building in the tile has the property `objectid`
3. On click: `GET /api/v1/features/{objectid}` → attributes, footprint, base height and height from the database

**Conventions** · coordinates and `bbox` are lon/lat (EPSG:4326) · pagination `limit` (max 500) + `offset` ·
every error is `{"error": {"code", "message", "details"}}` · reads need no token.

**Writes** (tag *edit*) need `Authorization: Bearer <WRITE_API_TOKEN>` (button *Authorize*) **and** the header
`X-User: <your name>` (stamped on the change). One write = 3DCityDB change + tile rebuild + new active tileset version.
Batch many writes with `?rebuild=false` and finish with `POST /layers/{layerId}/rebuild`.

**Files** · upload a CityJSON / CityGML / GeoJSON file into a layer with `POST /imports` (token, runs in the background, poll
`GET /imports/{jobId}`) · download a dataset with `GET /datasets/{code}/export?format=geojson|cityjson` (no token)."""

api = NinjaAPI(title="Digital Twin Catalog API", version="1.1", urls_namespace="catalog-api", description=DESCRIPTION)


def error(status, code, message, details=None):
    return status, {"error": {"code": code, "message": message, "details": details or {}}}


@api.exception_handler(ValidationError)
def on_validation(request, exc):
    first = exc.errors[0] if exc.errors else {}
    field = ".".join(str(x) for x in first.get("loc", [])[1:]) or None
    _, body = error(422, "invalid_parameter", f"{field or 'parameter'}: {first.get('msg', 'invalid')}", {"field": field})
    return api.create_response(request, body, status=422)


@api.exception_handler(HttpError)
def on_http(request, exc):
    codes = {400: "bad_request", 401: "unauthorized", 404: "not_found", 409: "conflict", 413: "payload_too_large", 422: "invalid_parameter", 502: "tile_rebuild_failed", 503: "writes_disabled"}
    _, body = error(exc.status_code, codes.get(exc.status_code, "error"), str(exc))
    return api.create_response(request, body, status=exc.status_code)


@api.exception_handler(Exception)
def on_error(request, exc):
    _, body = error(500, "internal_error", "Unexpected error")
    return api.create_response(request, body, status=500)


def rows(sql, params=()):
    with connections["ro"].cursor() as cur:
        cur.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def parse_bbox(bbox):
    try:
        minx, miny, maxx, maxy = (float(v) for v in bbox.split(","))
    except ValueError:
        raise HttpError(422, "bbox must be minx,miny,maxx,maxy (lon/lat EPSG:4326)")
    if not (-180 <= minx < maxx <= 180 and -90 <= miny < maxy <= 90):
        raise HttpError(422, "bbox out of range or minx>=maxx or miny>=maxy")
    return minx, miny, maxx, maxy


def layer_item(r, base=""):   # base = this server's origin; turns a stored '/tiles/...' path into an absolute URL
    bbox = [float(r[k]) for k in ("bbox_minx", "bbox_miny", "bbox_maxx", "bbox_maxy")] if r["bbox_minx"] is not None else None
    tileset = None
    if r["tileset_id"]:
        tileset = {"id": str(r["tileset_id"]), "version": r["tileset_version"], "provider": r["provider"],
                   "ionAssetId": r["ion_asset_id"], "url": base.rstrip("/") + r["url"] if r["url"] and r["url"].startswith("/") else r["url"], "heightOffsetM": float(r["height_offset_m"]),
                   "publishedAt": r["published_at"].isoformat() if r["published_at"] else None}
    return {"layerId": str(r["layer_id"]), "title": r["title"],
            "dataset": {"code": r["dataset_code"], "name": r["dataset_name"], "generatedBy": r["generated_by"],
                        "attribution": r["attribution"], "verticalDatum": r["vertical_datum"]},
            "theme": r["theme_code"], "lod": r["lod"], "status": r["status"], "featureCount": r["feature_count"],
            "isStale": r["is_stale"], "bbox": bbox, "tileset": tileset}


@api.get("/health", tags=["service"], summary="Service and database status")
def health(request):
    """`200 {"status": "ok", "database": "ok"}` or `503 {"status": "degraded", "database": "unreachable"}`."""
    try:
        rows("SELECT 1")
        return {"status": "ok", "database": "ok"}
    except Exception:
        return api.create_response(request, {"status": "degraded", "database": "unreachable"}, status=503)


@api.get("/catalog", tags=["catalog"], summary="Layers with their active tileset")
def catalog(request, lod: Optional[int] = Query(None, ge=0, le=4), theme: Optional[str] = None,
            dataset: Optional[str] = None, status: str = "published", bbox: Optional[str] = None,
            limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    """Entry point for the frontend: every layer (dataset × theme × LOD) with its **active** tileset.

    Filters: `dataset` (e.g. `oikn`), `theme` (`building`), `lod` (0–4), `bbox` (minLon,minLat,maxLon,maxLat; layers whose extent
    intersects it), `status` (`published` by default; `draft`, `validated`, `archived` or `all`).

    Item fields: `layerId`, `title`, `dataset {code, name, generatedBy, attribution, verticalDatum}`, `theme`, `lod`, `status`,
    `featureCount`, `isStale` (data changed after the tiles were built), `bbox`, and `tileset` (null when the layer has none):
    `{id, version, provider: self_hosted|cesium_ion, url (absolute, self_hosted), ionAssetId (cesium_ion), heightOffsetM, publishedAt}`.

    ```json
    {"items": [{"layerId": "ec5f6a15-…", "title": "OIKN buildings LOD1", "dataset": {"code": "oikn", "name": "OIKN", …},
      "theme": "building", "lod": 1, "status": "published", "featureCount": 389, "isStale": false,
      "bbox": [116.696, -0.987, 116.722, -0.958],
      "tileset": {"version": 17, "provider": "self_hosted", "url": "https://…/tiles/oikn/building/lod1/v17/tileset.json", "heightOffsetM": 0.0, …}}],
     "limit": 50, "offset": 0, "total": 1}
    ```
    Apply `heightOffsetM` as a vertical shift in metres. Errors: 422 (bad filter value)."""
    valid = {r["code"] for r in rows("SELECT code FROM catalog.ref_layer_status")}
    if status != "all" and status not in valid:
        raise HttpError(422, f"status must be one of {sorted(valid)} or 'all'")
    where, p = [], []
    if status != "all": where.append("status = %s"); p.append(status)
    if lod is not None: where.append("lod = %s"); p.append(lod)
    if theme: where.append("theme_code = %s"); p.append(theme)
    if dataset: where.append("dataset_code = %s"); p.append(dataset)
    if bbox:
        where.append("bbox && ST_MakeEnvelope(%s, %s, %s, %s, 4326)"); p += parse_bbox(bbox)
    w = ("WHERE " + " AND ".join(where)) if where else ""
    total = rows(f"SELECT count(*) AS n FROM catalog.v_layer {w}", p)[0]["n"]
    items = rows(f"SELECT * FROM catalog.v_layer {w} ORDER BY dataset_code, theme_code, lod LIMIT %s OFFSET %s", p + [limit, offset])
    return {"items": [layer_item(r, request.build_absolute_uri("/")) for r in items], "limit": limit, "offset": offset, "total": total}


@api.get("/layers/{layer_id}/tileset", tags=["catalog"], summary="Active tileset of one layer")
def layer_tileset(request, layer_id: str):
    """The `tileset` object of `/catalog` for one `layerId`. Errors: 404 (no layer or no active tileset), 422 (not a UUID)."""
    try:
        r = rows("SELECT * FROM catalog.v_layer WHERE layer_id = %s", [layer_id])
    except Exception:
        raise HttpError(422, "layer_id must be a UUID")
    if not r or not r[0]["tileset_id"]:
        raise HttpError(404, "Layer not found or it has no active tileset")
    return layer_item(r[0], request.build_absolute_uri("/"))["tileset"]


@api.get("/datasets", tags=["datasets"], summary="Active datasets")
def datasets(request, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    """`{items: [{code, name, region, generatedBy, verticalDatum, attribution, layers}], limit, offset, total}`."""
    total = rows("SELECT count(*) AS n FROM catalog.dataset WHERE status = 'active'")[0]["n"]
    items = rows("""SELECT d.code, d.name, d.region, d.generated_by, d.vertical_datum, d.attribution,
                           (SELECT count(*) FROM catalog.layer l WHERE l.dataset_id = d.id AND l.status <> 'archived') AS layers
                    FROM catalog.dataset d WHERE d.status = 'active' ORDER BY d.code LIMIT %s OFFSET %s""", [limit, offset])
    return {"items": [{"code": r["code"], "name": r["name"], "region": r["region"], "generatedBy": r["generated_by"],
                       "verticalDatum": r["vertical_datum"], "attribution": r["attribution"], "layers": r["layers"]} for r in items],
            "limit": limit, "offset": offset, "total": total}


@api.get("/datasets/{code}", tags=["datasets"], summary="One dataset with its layers")
def dataset(request, code: str):
    """Dataset metadata (incl. `description`, `license`) and its non-archived layers (same item shape as `/catalog`,
    tileset URLs relative). Errors: 404."""
    d = rows("SELECT code, name, description, region, generated_by, vertical_datum, attribution, license FROM catalog.dataset WHERE code = %s", [code])
    if not d:
        raise HttpError(404, "Dataset not found")
    layers = rows("SELECT * FROM catalog.v_layer WHERE dataset_code = %s AND status <> 'archived' ORDER BY theme_code, lod", [code])
    r = d[0]
    return {"code": r["code"], "name": r["name"], "description": r["description"], "region": r["region"],
            "generatedBy": r["generated_by"], "verticalDatum": r["vertical_datum"], "attribution": r["attribution"],
            "license": r["license"], "layers": [layer_item(x) for x in layers]}


@api.get("/datasets/{code}/stats", tags=["datasets"], summary="Object counts per class and LOD")
def dataset_stats(request, code: str):
    """`{dataset, items: [{theme, class, lod, topLevel, featureCount}]}`. Errors: 404."""
    if not rows("SELECT 1 FROM catalog.dataset WHERE code = %s", [code]):
        raise HttpError(404, "Dataset not found")
    st = rows("""SELECT theme_code, classname, lod, feature_count, is_toplevel FROM catalog.layer_feature_stats
                 WHERE dataset_code = %s ORDER BY theme_code, classname, lod""", [code])
    return {"dataset": code, "items": [{"theme": r["theme_code"], "class": r["classname"], "lod": r["lod"] or None,
                                        "topLevel": r["is_toplevel"], "featureCount": r["feature_count"]} for r in st]}


@api.get("/datasets/{code}/export", tags=["datasets"], summary="Download a dataset (GeoJSON footprints or CityJSON)")
def dataset_export(request, code: str, format: str = "geojson"):
    """Download every current top-level object of a dataset (all its layers) as a file (`Content-Disposition: attachment`).

    `format=geojson` (default): FeatureCollection in lon/lat. Geometry = the real footprint (ground face of the LOD1 solid;
    null for objects without one). Properties are flat so GIS tools read them directly:
    `objectid`, `class`, `lineage`, `base_z` (ground height in the layer's height system), `height_m`, and every generic attribute.
    ```json
    {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[116.69733, -0.97186], …]]},
     "properties": {"objectid": "KIPP_0000", "class": "Building", "lineage": "oikn.building.lod1", "base_z": 28.91, "height_m": 4.37,
                    "nama": "ALFAMART IKN_BO", "h_ground": 28.91, …}}
    ```
    This file can be uploaded again with `POST /imports` (use new `objectid`s or delete the old objects first).

    `format=cityjson`: CityJSON 2.0 written by citydb-tool from 3DCityDB (full geometry, all LODs, model CRS e.g. EPSG:32750).
    Both formats take 1–2 s for the 389 KIPP buildings. Errors: 404 (dataset), 422 (format)."""
    if format not in ("geojson", "cityjson"): raise HttpError(422, "format must be geojson or cityjson")
    if not rows("SELECT 1 FROM catalog.dataset WHERE code = %s", [code]): raise HttpError(404, "Dataset not found")
    lineages = [f"{code}.{r['theme_code']}.lod{r['lod']}" for r in rows(
        "SELECT l.theme_code, l.lod FROM catalog.layer l JOIN catalog.dataset d ON d.id = l.dataset_id WHERE d.code = %s", [code])]
    from django.http import HttpResponse
    if format == "cityjson":
        if not lineages: raise HttpError(404, "Dataset has no layers")
        body, ext, ctype = edit.export_cityjson(lineages), "city.json", "application/city+json"
    else:
        body, ext, ctype = json.dumps(export_geojson(lineages)), "geojson", "application/geo+json"
    resp = HttpResponse(body, content_type=ctype)
    resp["Content-Disposition"] = f'attachment; filename="{code}.{ext}"'
    return resp


def export_geojson(lineages):
    feats = rows("""WITH f AS (SELECT f.id, f.objectid, f.lineage, oc.classname FROM citydb.feature f JOIN citydb.objectclass oc ON oc.id = f.objectclass_id
                               WHERE f.lineage = ANY(%s) AND f.termination_date IS NULL AND oc.is_toplevel = 1),
                         s AS (SELECT p.feature_id, ST_ZMin(gd.geometry) AS z0, ST_ZMax(gd.geometry) AS z1, (ST_Dump(gd.geometry)).geom AS face
                               FROM f JOIN citydb.property p ON p.feature_id = f.id AND p.name = 'lod1Solid'
                               JOIN citydb.geometry_data gd ON gd.id = p.val_geometry_id)
                    SELECT f.id, f.objectid, f.lineage, f.classname, min(s.z0) AS base_z, max(s.z1) - min(s.z0) AS height_m,
                           ST_AsGeoJSON(ST_Transform(ST_Collect(ST_Force2D(s.face)) FILTER (WHERE ST_ZMax(s.face) - s.z0 < 1e-6), 4326), 7) AS geom
                    FROM f LEFT JOIN s ON s.feature_id = f.id
                    GROUP BY f.id, f.objectid, f.lineage, f.classname ORDER BY f.id""", [lineages])
    attrs = {}
    for a in rows("""SELECT p.feature_id, p.name, p.val_int, p.val_double, p.val_string, p.val_timestamp, p.val_uri
                     FROM citydb.property p JOIN citydb.feature f ON f.id = p.feature_id JOIN citydb.namespace n ON n.id = p.namespace_id
                     WHERE f.lineage = ANY(%s) AND f.termination_date IS NULL AND p.parent_id IS NULL AND n.alias = 'gen'""", [lineages]):
        attrs.setdefault(a["feature_id"], {})[a["name"]] = value_of(a)
    out = []
    for r in feats:
        g = json.loads(r["geom"]) if r["geom"] else None
        if g and g["type"] == "MultiPolygon" and len(g["coordinates"]) == 1: g = {"type": "Polygon", "coordinates": g["coordinates"][0]}   # one ground face
        out.append({"type": "Feature", "geometry": g,
                    "properties": {**attrs.get(r["id"], {}), "objectid": r["objectid"], "class": r["classname"], "lineage": r["lineage"],
                                   "base_z": round(r["base_z"], 3) if r["base_z"] is not None else None,
                                   "height_m": round(r["height_m"], 3) if r["height_m"] is not None else None}})
    return {"type": "FeatureCollection", "features": out}


def value_of(r):
    for k in ("val_double", "val_int", "val_string", "val_timestamp", "val_uri"):
        if r[k] is not None:
            v = r[k]
            return v.isoformat() if hasattr(v, "isoformat") else v
    return None


@api.get("/features/{objectid}", tags=["features"], summary="Attributes of one city object (gml:id)")
def feature(request, objectid: str, dataset: Optional[str] = None):
    """Everything the database knows about one object; call it with the `objectid` read from the clicked tile feature.

    ```json
    {"objectid": "KIPP_0000", "class": "Building", "dataset": "oikn", "theme": "building", "lineage": "oikn.building.lod1",
     "geometry": {"footprint": [[[116.69733, -0.97186], …]], "baseZ": 28.91, "heightM": 4.37},
     "lodsAvailable": ["1"],
     "attributes": [{"name": "nama", "value": "ALFAMART IKN_BO", "uom": null}, {"name": "h_ground", "value": 28.91, "uom": null}, …],
     "bbox": [116.69712, -0.97186, 116.69740, -0.97155], "createdAt": "…", "modifiedAt": "…", "modifiedBy": "fairuz"}
    ```
    `geometry` is filled for LOD1 buildings that are an extruded footprint (lon/lat rings, `baseZ` ground height in the model's
    height system, `heightM` building height), otherwise null. Pass `dataset` when the same id exists in several datasets.
    Errors: 404, 409 (id in several datasets)."""
    where, p = ["f.objectid = %s", "f.termination_date IS NULL"], [objectid]
    if dataset:
        where.append("(f.lineage = %s OR f.lineage LIKE %s)"); p += [dataset, dataset.replace("_", "\\_") + ".%"]
    found = rows(f"""SELECT f.id, f.objectid, oc.classname, f.lineage, f.creation_date, f.last_modification_date, f.updating_person,
                            ST_XMin(ST_Transform(f.envelope, 4326)) AS x0, ST_YMin(ST_Transform(f.envelope, 4326)) AS y0,
                            ST_XMax(ST_Transform(f.envelope, 4326)) AS x1, ST_YMax(ST_Transform(f.envelope, 4326)) AS y1
                     FROM citydb.feature f JOIN citydb.objectclass oc ON oc.id = f.objectclass_id
                     WHERE {' AND '.join(where)} LIMIT 2""", p)
    if not found:
        raise HttpError(404, "Feature not found")
    if len(found) > 1:
        raise HttpError(409, "objectid exists in several datasets; pass ?dataset=<code>")
    f = found[0]
    attrs = rows("""SELECT name, val_int, val_double, val_string, val_timestamp, val_uri, val_uom FROM citydb.property
                    WHERE feature_id = %s AND val_geometry_id IS NULL AND val_implicitgeom_id IS NULL AND val_feature_id IS NULL
                      AND (val_int IS NOT NULL OR val_double IS NOT NULL OR val_string IS NOT NULL OR val_timestamp IS NOT NULL OR val_uri IS NOT NULL)
                    ORDER BY name""", [f["id"]])
    lods = rows("SELECT DISTINCT val_lod FROM citydb.property WHERE feature_id = %s AND val_lod IS NOT NULL ORDER BY 1", [f["id"]])
    parts = (f["lineage"] or "").split(".")
    geometry = None                                   # block-model summary, for LOD1 objects with a plain extruded footprint
    if f["classname"] == "Building" and (f["lineage"] or "").endswith(".lod1"):
        b = edit.read_building(f["objectid"])
        if b and b["simple"]:
            geometry = {"footprint": edit.model_to_lonlat(b["rings"], edit.db_srid()), "baseZ": b["base_z"], "heightM": b["height_m"]}
    return {"objectid": f["objectid"], "class": f["classname"], "geometry": geometry,
            "dataset": parts[0] or None, "theme": parts[1] if len(parts) > 1 else None, "lineage": f["lineage"],
            "lodsAvailable": [r["val_lod"] for r in lods],
            "attributes": [{"name": a["name"], "value": value_of(a), "uom": a["val_uom"]} for a in attrs],
            "bbox": [float(f[k]) for k in ("x0", "y0", "x1", "y1")] if f["x0"] is not None else None,
            "createdAt": f["creation_date"].isoformat() if f["creation_date"] else None,
            "modifiedAt": f["last_modification_date"].isoformat() if f["last_modification_date"] else None,
            "modifiedBy": f["updating_person"]}


@api.get("/features", tags=["features"], summary="Search city objects (GeoJSON, envelope footprints)")
def features(request, dataset: Optional[str] = None, bbox: Optional[str] = None, q: Optional[str] = None,
             limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    """GeoJSON FeatureCollection of top-level objects for lists, search and 2D maps.
    `q` matches the `objectid` or the `name`/`nama` attribute (case-insensitive, substring), e.g. `q=MASJID`.
    Geometry is the object's bounding box (lon/lat), not the exact footprint; use `/features/{objectid}` for that.
    Properties: `objectid`, `class`, `lineage`, `name`. Errors: 422 (bad bbox)."""
    where, p = ["f.lineage IS NOT NULL", "f.termination_date IS NULL", "oc.is_toplevel = 1"], []
    if dataset:
        where.append("(f.lineage = %s OR f.lineage LIKE %s)"); p += [dataset, dataset.replace("_", "\\_") + ".%"]
    if bbox:
        where.append("f.envelope && ST_Transform(ST_MakeEnvelope(%s, %s, %s, %s, 4326), (SELECT srid FROM citydb.database_srs LIMIT 1))")
        p += parse_bbox(bbox)
    if q:
        where.append("""(f.objectid ILIKE %s OR EXISTS (SELECT 1 FROM citydb.property pn WHERE pn.feature_id = f.id
                                                         AND pn.name IN ('name', 'nama') AND pn.val_string ILIKE %s))""")
        p += [f"%{q}%", f"%{q}%"]
    w = " AND ".join(where)
    res = rows(f"""SELECT f.objectid, oc.classname, f.lineage,
                          (SELECT pn.val_string FROM citydb.property pn WHERE pn.feature_id = f.id AND pn.name IN ('name', 'nama') ORDER BY pn.name LIMIT 1) AS name,
                          ST_AsGeoJSON(ST_Transform(ST_Force2D(ST_Envelope(f.envelope)), 4326)) AS geom
                   FROM citydb.feature f JOIN citydb.objectclass oc ON oc.id = f.objectclass_id
                   WHERE {w} ORDER BY f.id LIMIT %s OFFSET %s""", p + [limit, offset])
    import json
    return {"type": "FeatureCollection", "limit": limit, "offset": offset,
            "features": [{"type": "Feature", "geometry": json.loads(r["geom"]) if r["geom"] else None,
                          "properties": {"objectid": r["objectid"], "class": r["classname"], "lineage": r["lineage"], "name": r["name"]}}
                         for r in res]}


# ---------------------------------------------------------------- write API (token required)
from typing import Dict, List, Union
from . import edit

Attr = Union[str, float, int, bool, None]


class BuildingIn(Schema):
    dataset: str
    theme: str = "building"
    objectid: Optional[str] = None                 # generated (API_xxxxxxxxxx) when omitted
    footprint: List[List[float]]                   # outer ring, [lon, lat] points (EPSG:4326)
    holes: List[List[List[float]]] = []            # optional inner rings
    height_m: float
    base_z: Optional[float] = None                 # model height of the ground; default = mean ground of the layer
    attributes: Dict[str, Attr] = {}


class BuildingPatch(Schema):
    footprint: Optional[List[List[float]]] = None
    holes: Optional[List[List[List[float]]]] = None
    height_m: Optional[float] = None
    base_z: Optional[float] = None
    attributes: Optional[Dict[str, Attr]] = None   # merged into the existing ones; a null value removes that attribute


def _check_height(h):
    if not (0 < h <= 1000): raise HttpError(422, "height_m must be between 0 and 1000")


@api.post("/features", tags=["edit"], auth=edit.WRITER, summary="Create a LOD1 building (token required)", response={201: dict})
def create_feature(request, body: BuildingIn, rebuild: bool = True):
    """Add one LOD1 block building (footprint extruded to a height) to an existing layer.

    Body: `dataset` (layer must exist), `theme` (default `building`), `objectid` (optional, unique in the whole database,
    letters/digits/`_.-`, max 64; generated `API_…` when omitted), `footprint` (outer ring of [lon, lat], ≥ 3 points, valid,
    ≥ 1 m²), `holes` (inner rings), `height_m` (0 < h ≤ 1000), `base_z` (ground height in the layer's height system, e.g.
    KIPP `h_ground`; default = mean ground of the layer), `attributes` (name → string/number).

    ```json
    {"dataset": "oikn", "objectid": "KIPP_0389", "footprint": [[116.7090,-0.9720],[116.7094,-0.9720],[116.7094,-0.9717],[116.7090,-0.9717]],
     "height_m": 25, "base_z": 26.3, "attributes": {"nama": "Gedung Baru", "lantai": 6}}
    ```
    `201 {"objectid", "action": "created", "jobId", "layerId", "tileset": {"version", "url", "buildings"}, "stale": false}`
    (`tileset` null and `stale` true with `?rebuild=false`). Takes ~10–20 s with rebuild.
    Errors: 400 (no X-User), 401, 404 (layer), 409 (objectid exists), 422 (geometry/height/attribute name), 502 (saved, tile rebuild failed), 503."""
    user = request.auth
    objectid = body.objectid or "API_" + uuid.uuid4().hex[:10]
    if not edit.ID_RE.fullmatch(objectid): raise HttpError(422, "objectid may contain letters, digits, '_', '.', '-' (max 64)")
    _check_height(body.height_m)
    if edit.feature_row(objectid): raise HttpError(409, f"objectid {objectid} already exists")
    srid = edit.db_srid()
    lineage = f"{body.dataset}.{body.theme}.lod1"
    edit.layer_row(body.dataset, body.theme, 1)
    rings = edit.lonlat_to_model([body.footprint, *body.holes], srid)
    base = body.base_z if body.base_z is not None else edit.mean_ground_z(lineage)
    gml = edit.building_gml(objectid, {k: v for k, v in body.attributes.items() if v is not None}, rings, base, body.height_m, srid)

    def work(cur, layer):
        edit.import_gml(gml, lineage, user, f"api:create:{objectid}")
        return {"objectid": objectid, "action": "created", "job": ("import_all", f"api:POST:{objectid}", gml, {"Building": 1})}
    return 201, edit.operation(user, body.dataset, body.theme, 1, work, rebuild)


@api.patch("/features/{objectid}", tags=["edit"], auth=edit.WRITER, summary="Update attributes and/or geometry of a building (token required)")
def update_feature(request, objectid: str, body: BuildingPatch, rebuild: bool = True):
    """Send only what changes: `footprint` (+ `holes`), `height_m`, `base_z`, `attributes`. Attributes are merged into the existing
    ones; a `null` value removes that attribute. Example: `{"height_m": 40, "attributes": {"lantai": 10, "catatan": null}}`.
    Internally delete + re-import of that one building; if the re-import fails the previous block is restored.
    Response as for create with `action: "updated"`. Errors: 400, 401, 404, 422 (also when the stored geometry is not a simple
    extruded footprint and no new `footprint` + `height_m` is sent), 502, 503."""
    user = request.auth
    cur_b = edit.read_building(objectid)
    if not cur_b: raise HttpError(404, f"Object {objectid} not found")
    if not cur_b["simple"] and not (body.footprint and body.height_m):
        raise HttpError(422, "This building's geometry is not a simple extruded footprint; send footprint and height_m to replace it.")
    srid = edit.db_srid()
    dataset, theme, lod = edit.parse_lineage(cur_b["lineage"])
    if lod != 1: raise HttpError(422, "Only LOD1 objects can be edited through the API")
    rings = (edit.lonlat_to_model([body.footprint, *(body.holes or [])], srid) if body.footprint
             else [r for r in cur_b["rings"]] if body.holes is None else edit.lonlat_to_model([edit.model_to_lonlat(cur_b["rings"], srid)[0], *body.holes], srid))
    height = body.height_m if body.height_m is not None else cur_b["height_m"]
    base = body.base_z if body.base_z is not None else cur_b["base_z"]
    _check_height(height)
    attrs = {**cur_b["attributes"], **(body.attributes or {})}
    attrs = {k: v for k, v in attrs.items() if v is not None}
    gml = edit.building_gml(objectid, attrs, rings, base, height, srid)
    old = edit.building_gml(objectid, cur_b["attributes"], cur_b["rings"], cur_b["base_z"], cur_b["height_m"], srid) if cur_b["simple"] else None
    lineage = cur_b["lineage"]

    def work(cur, layer):
        edit.delete_ids(objectid, lineage)
        try:
            edit.import_gml(gml, lineage, user, f"api:update:{objectid}")
        except Exception:
            if old: edit.import_gml(old, lineage, user, f"api:update-rollback:{objectid}")   # compensate: restore the previous block
            raise
        return {"objectid": objectid, "action": "updated", "job": ("import_all", f"api:PATCH:{objectid}", gml, {"Building": 1})}
    return edit.operation(user, dataset, theme, 1, work, rebuild)


@api.delete("/features/{objectid}", tags=["edit"], auth=edit.WRITER, summary="Delete a building (token required)")
def delete_feature(request, objectid: str, rebuild: bool = True):
    """Remove one building from 3DCityDB and from the tiles. Response as for create with `action: "deleted"`.
    Errors: 400, 401, 404, 409 (it is the last building of its layer), 502, 503."""
    user = request.auth
    f = edit.feature_row(objectid)
    if not f: raise HttpError(404, f"Object {objectid} not found")
    dataset, theme, lod = edit.parse_lineage(f["lineage"])
    n = rows("SELECT count(*) AS n FROM citydb.feature WHERE lineage = %s AND termination_date IS NULL AND objectclass_id = (SELECT objectclass_id FROM citydb.feature WHERE id = %s)", [f["lineage"], f["id"]])[0]["n"]
    if n <= 1: raise HttpError(409, "A layer must keep at least one building.")

    def work(cur, layer):
        edit.delete_ids(objectid, f["lineage"])
        return {"objectid": objectid, "action": "deleted", "job": ("delete", f"api:DELETE:{objectid}", objectid, {"Building": 1})}
    return edit.operation(user, dataset, theme, lod, work, rebuild)


@api.post("/layers/{layer_id}/rebuild", tags=["edit"], auth=edit.WRITER, summary="Rebuild and activate the tileset from the current database (token required)")
def rebuild_layer(request, layer_id: str):
    """Rebuild the layer's 3D Tiles from the current database and make them the active version
    (`{layerId, tileset: {version, url, buildings}}`). Use it after a batch of `?rebuild=false` writes or when `isStale` is true.
    Errors: 400, 401, 404, 422 (not a UUID), 503."""
    user = request.auth
    try:
        r = rows("SELECT dataset_code, theme_code, lod FROM catalog.v_layer WHERE layer_id = %s", [layer_id])
    except Exception:
        raise HttpError(422, "layer_id must be a UUID")
    if not r: raise HttpError(404, "Layer not found")
    ds, th, lod = r[0]["dataset_code"], r[0]["theme_code"], r[0]["lod"]
    layer = edit.layer_row(ds, th, lod)
    with edit.transaction.atomic(using="default"):
        with edit.connections["default"].cursor() as cur:
            cur.execute("SELECT set_config('app.user', %s, true)", [f"api:{user}"])
            cur.execute("SELECT pg_advisory_xact_lock(%s)", [edit.LOCK_KEY])
            v, url, n = edit.sync_tiles(cur, layer, ds, th, lod)
    return {"layerId": layer_id, "tileset": {"version": v, "url": url, "buildings": n}}


MAX_UPLOAD = 200 * 1024 * 1024


@api.post("/imports", tags=["edit"], auth=edit.WRITER, summary="Upload a file into a layer (token required, runs in the background)", response={202: dict})
def create_import(request, file: UploadedFile = File(...), dataset: str = Form(...), theme: str = Form("building"), lod: int = Form(1)):
    """Multipart upload (`file` + form fields `dataset`, `theme` = `building`, `lod` = 1) into an **existing** layer. Formats
    (detected from the content):
    - **CityJSON 2.0** or **CityGML 2.0**: imported as is by citydb-tool; the file's CRS must be declared in it
      (CityJSON `metadata.referenceSystem`, GML `srsName`), coordinates are transformed to the database CRS.
    - **GeoJSON** FeatureCollection of lon/lat footprints (Polygon, or MultiPolygon with one part), turned into LOD1 blocks
      exactly like `POST /features`. Properties: `height_m` (required), `base_z` (optional, default = mean ground of the layer),
      `objectid` (optional, else the feature `id`, else generated), every other property becomes an attribute. Features that fail
      a check (geometry, height, objectid already in the database) are skipped and listed in `report.skipped`; the rest is imported.
      The output of `GET /datasets/{code}/export?format=geojson` has this shape.

    The whole file is one citydb-tool import, one `import_job` and (LOD1 layers) one tile rebuild. That takes from ~15 s to
    minutes, longer than an HTTP request should wait, so the call returns at once:
    `202 {"jobId": "…", "status": "running", "statusUrl": "/api/v1/imports/…"}`. Poll `GET /imports/{jobId}` every few seconds.

    ```bash
    curl -X POST $API/api/v1/imports -H "Authorization: Bearer $TOKEN" -H "X-User: fairuz" \
         -F file=@KIPP_LOD1.city.json -F dataset=oikn -F theme=building -F lod=1
    ```
    Objects keep the ids in the file; CityJSON/CityGML ids are not checked against existing objects, so importing the same file
    twice duplicates the buildings. Max 200 MB. Errors: 400 (no X-User), 401, 404 (layer), 413, 422 (unknown format), 503."""
    user = request.auth
    if file.size and file.size > MAX_UPLOAD: raise HttpError(413, "file is larger than 200 MB")
    fmt = edit.sniff_format(file.read(4096)); file.seek(0)
    if not fmt: raise HttpError(422, "file must be CityJSON, CityGML (XML) or a GeoJSON FeatureCollection")
    if not (0 <= lod <= 4): raise HttpError(422, "lod must be 0-4")
    job = edit.start_import(user, dataset, theme, lod, file, fmt)
    return 202, {"jobId": job, "status": "running", "format": fmt, "statusUrl": f"/api/v1/imports/{job}"}


def as_json(v):
    return json.loads(v) if isinstance(v, str) else v


@api.get("/imports/{job_id}", tags=["edit"], summary="Status of an upload / import job")
def import_status(request, job_id: str):
    """State of any import job (uploads, API writes, `scripts/import.sh`). No token needed.

    ```json
    {"jobId": "…", "status": "succeeded", "dataset": "oikn", "theme": "building", "lod": 1, "layerId": "…",
     "sourceFile": "KIPP_LOD1.city.json", "sizeBytes": 1218757, "sha256": "…", "counts": {"Building": 389},
     "report": {"format": "cityjson", "tileset": {"version": 3, "url": "/tiles/…/v3/tileset.json", "buildings": 389}},
     "startedAt": "…", "finishedAt": "…", "createdBy": "api:fairuz"}
    ```
    `status`: `running` → `succeeded` | `failed`. `counts`: imported top-level objects per class. `report`: `format`,
    `tileset` (new active tileset, null for non-LOD1 layers), `skipped` (GeoJSON features not imported, with reason),
    `tileError` (data saved, tiles not rebuilt: call `POST /layers/{layerId}/rebuild`), `error` (why it failed; nothing was imported).
    Errors: 404, 422 (not a UUID)."""
    try:
        r = rows("""SELECT j.*, d.code, l.id AS layer_id, l.theme_code, l.lod FROM catalog.import_job j JOIN catalog.dataset d ON d.id = j.dataset_id
                    LEFT JOIN catalog.import_job_layer jl ON jl.job_id = j.id LEFT JOIN catalog.layer l ON l.id = jl.layer_id
                    WHERE j.id = %s LIMIT 1""", [job_id])
    except Exception:
        raise HttpError(422, "job_id must be a UUID")
    if not r: raise HttpError(404, "Import job not found")
    j = r[0]
    return {"jobId": str(j["id"]), "status": j["status"], "dataset": j["code"], "theme": j["theme_code"], "lod": j["lod"],
            "layerId": str(j["layer_id"]) if j["layer_id"] else None, "sourceFile": j["source_file"], "sizeBytes": j["source_size_bytes"],
            "sha256": j["source_sha256"], "counts": as_json(j["counts"]), "report": as_json(j["report"]),
            "startedAt": j["started_at"].isoformat(), "finishedAt": j["finished_at"].isoformat() if j["finished_at"] else None,
            "createdBy": j["created_by"]}
