"""API /api/v1 (django-ninja). Reads use the SELECT-only DB alias `ro`. Writes (POST/PATCH/DELETE on /features, POST /layers/{id}/rebuild)
are in catalog/edit.py, need a bearer token and keep the served tileset in sync."""
import uuid
from typing import Optional

from django.db import connections
from ninja import NinjaAPI, Query, Schema
from ninja.errors import HttpError, ValidationError

api = NinjaAPI(title="Digital Twin Catalog API", version="1.0", urls_namespace="catalog-api",
               description="Layers and tilesets for the Cesium frontend, city object attributes from 3DCityDB, and (with a write token) create/update/delete of LOD1 buildings with automatic tile rebuild.")


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
    codes = {400: "bad_request", 401: "unauthorized", 404: "not_found", 409: "conflict", 422: "invalid_parameter", 502: "tile_rebuild_failed", 503: "writes_disabled"}
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


@api.get("/health", tags=["service"])
def health(request):
    try:
        rows("SELECT 1")
        return {"status": "ok", "database": "ok"}
    except Exception:
        return api.create_response(request, {"status": "degraded", "database": "unreachable"}, status=503)


@api.get("/catalog", tags=["catalog"], summary="Layers with their active tileset")
def catalog(request, lod: Optional[int] = Query(None, ge=0, le=4), theme: Optional[str] = None,
            dataset: Optional[str] = None, status: str = "published", bbox: Optional[str] = None,
            limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
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


@api.get("/layers/{layer_id}/tileset", tags=["catalog"])
def layer_tileset(request, layer_id: str):
    try:
        r = rows("SELECT * FROM catalog.v_layer WHERE layer_id = %s", [layer_id])
    except Exception:
        raise HttpError(422, "layer_id must be a UUID")
    if not r or not r[0]["tileset_id"]:
        raise HttpError(404, "Layer not found or it has no active tileset")
    return layer_item(r[0], request.build_absolute_uri("/"))["tileset"]


@api.get("/datasets", tags=["datasets"])
def datasets(request, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    total = rows("SELECT count(*) AS n FROM catalog.dataset WHERE status = 'active'")[0]["n"]
    items = rows("""SELECT d.code, d.name, d.region, d.generated_by, d.vertical_datum, d.attribution,
                           (SELECT count(*) FROM catalog.layer l WHERE l.dataset_id = d.id AND l.status <> 'archived') AS layers
                    FROM catalog.dataset d WHERE d.status = 'active' ORDER BY d.code LIMIT %s OFFSET %s""", [limit, offset])
    return {"items": [{"code": r["code"], "name": r["name"], "region": r["region"], "generatedBy": r["generated_by"],
                       "verticalDatum": r["vertical_datum"], "attribution": r["attribution"], "layers": r["layers"]} for r in items],
            "limit": limit, "offset": offset, "total": total}


@api.get("/datasets/{code}", tags=["datasets"])
def dataset(request, code: str):
    d = rows("SELECT code, name, description, region, generated_by, vertical_datum, attribution, license FROM catalog.dataset WHERE code = %s", [code])
    if not d:
        raise HttpError(404, "Dataset not found")
    layers = rows("SELECT * FROM catalog.v_layer WHERE dataset_code = %s AND status <> 'archived' ORDER BY theme_code, lod", [code])
    r = d[0]
    return {"code": r["code"], "name": r["name"], "description": r["description"], "region": r["region"],
            "generatedBy": r["generated_by"], "verticalDatum": r["vertical_datum"], "attribution": r["attribution"],
            "license": r["license"], "layers": [layer_item(x) for x in layers]}


@api.get("/datasets/{code}/stats", tags=["datasets"])
def dataset_stats(request, code: str):
    if not rows("SELECT 1 FROM catalog.dataset WHERE code = %s", [code]):
        raise HttpError(404, "Dataset not found")
    st = rows("""SELECT theme_code, classname, lod, feature_count, is_toplevel FROM catalog.layer_feature_stats
                 WHERE dataset_code = %s ORDER BY theme_code, classname, lod""", [code])
    return {"dataset": code, "items": [{"theme": r["theme_code"], "class": r["classname"], "lod": r["lod"] or None,
                                        "topLevel": r["is_toplevel"], "featureCount": r["feature_count"]} for r in st]}


def value_of(r):
    for k in ("val_double", "val_int", "val_string", "val_timestamp", "val_uri"):
        if r[k] is not None:
            v = r[k]
            return v.isoformat() if hasattr(v, "isoformat") else v
    return None


@api.get("/features/{objectid}", tags=["features"], summary="Attributes of one city object (gml:id)")
def feature(request, objectid: str, dataset: Optional[str] = None):
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
    where, p = ["f.lineage IS NOT NULL", "f.termination_date IS NULL", "oc.is_toplevel = 1"], []
    if dataset:
        where.append("(f.lineage = %s OR f.lineage LIKE %s)"); p += [dataset, dataset.replace("_", "\\_") + ".%"]
    if bbox:
        where.append("f.envelope && ST_Transform(ST_MakeEnvelope(%s, %s, %s, %s, 4326), (SELECT srid FROM citydb.database_srs LIMIT 1))")
        p += parse_bbox(bbox)
    if q:
        where.append("""EXISTS (SELECT 1 FROM citydb.property pn WHERE pn.feature_id = f.id AND pn.name = 'name' AND pn.val_string ILIKE %s)""")
        p.append(f"%{q}%")
    w = " AND ".join(where)
    res = rows(f"""SELECT f.objectid, oc.classname, f.lineage,
                          (SELECT pn.val_string FROM citydb.property pn WHERE pn.feature_id = f.id AND pn.name = 'name' LIMIT 1) AS name,
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


@api.post("/features", tags=["edit"], auth=edit.require_writer, summary="Create a LOD1 building (token required)", response={201: dict})
def create_feature(request, body: BuildingIn, rebuild: bool = True):
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


@api.patch("/features/{objectid}", tags=["edit"], auth=edit.require_writer, summary="Update attributes and/or geometry of a building (token required)")
def update_feature(request, objectid: str, body: BuildingPatch, rebuild: bool = True):
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


@api.delete("/features/{objectid}", tags=["edit"], auth=edit.require_writer, summary="Delete a building (token required)")
def delete_feature(request, objectid: str, rebuild: bool = True):
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


@api.post("/layers/{layer_id}/rebuild", tags=["edit"], auth=edit.require_writer, summary="Rebuild and activate the tileset from the current database (token required)")
def rebuild_layer(request, layer_id: str):
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
