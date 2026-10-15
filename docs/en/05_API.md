# Introduction

Status: **v1.1, live** · Test server `https://twindb.178-105-182-162.sslip.io` (Hetzner, see `docs/PANDUAN_HETZNER.md`) · Base path `/api/v1` · JSON · reads are open, writes need a bearer token (see README section 5.1) · OpenAPI at `/api/v1/openapi.json`, UI at `/api/v1/docs` (django-ninja). Every endpoint carries a description with an example there; write endpoints use the *Authorize* button (bearer) plus the `X-User` header.


# Endpoints
| Endpoint | Purpose |
|---|---|
| `GET /health` | Service + database status |
| `GET /catalog?lod=&theme=&dataset=&bbox=minx,miny,maxx,maxy&status=published&limit=&offset=` | Layers with active tileset (source: `catalog.v_layer`) |
| `GET /layers/{id}/tileset` | Active tileset of a layer |
| `GET /datasets`, `GET /datasets/{code}` | Datasets and their layers per LOD |
| `GET /datasets/{code}/stats` | Object counts per class/LOD |
| `GET /datasets/{code}/export?format=geojson\|cityjson` | **Download** every current object of the dataset as a file: GeoJSON (real footprints + `base_z` + `height_m` + attributes, lon/lat) or CityJSON 2.0 exported by citydb-tool |
| `POST /imports` *(token, multipart)*, `GET /imports/{jobId}` | **Upload** a CityJSON 2.0 / CityGML 2.0 file, or a GeoJSON of footprints, into an existing layer; runs in the background, poll the job (section "File upload and download") |
| `GET /features/{objectid}` | Attributes of one city object from `citydb` (name, class, height, function, usage, generic attributes, LODs available, dataset, lineage) |
| `POST /features`, `PATCH /features/{objectid}`, `DELETE /features/{objectid}`, `POST /layers/{id}/rebuild`, `PUT /layers/{id}/status` | **Write API (token).** Create/update/delete a LOD1 building; the served tileset is rebuilt from the database and activated in the same request. Verified end to end on 9 October 2026 with `scripts/crud_smoke.py` (23 checks) and on the Hetzner server on 15 October 2026 (18 checks, `docs/LAPORAN_INPUT_DATA_KIPP.md`). Details: README section 5.1 |
| `GET /features?dataset=&bbox=&q=&limit=` | GeoJSON for search and 2D lists: bounding-box geometry + `objectid`, `class`, `lineage`, `name`. `q` matches the `objectid` or the `name`/`nama` attribute (e.g. `q=MASJID`) |

Rules: `bbox` is lon/lat EPSG:4326; `lod` 0–4, empty = all; `status` default `published`; pagination `limit` (default 50, max 500) + `offset`; unknown filter value → 422.

Error format (all errors):
```json
{ "error": { "code": "invalid_parameter", "message": "lod must be between 0 and 4", "details": {"field": "lod"} } }
```

Catalog item:
```json
{ "layerId": "uuid", "dataset": {"code":"oikn","name":"OIKN","generatedBy":"external","attribution":null},
  "theme": "building", "lod": 1, "status": "published", "featureCount": 0, "isStale": false,
  "bbox": [116.6,-1.0,116.9,-0.8],
  "tileset": {"version":17,"provider":"self_hosted","ionAssetId":null,
              "url":"https://twindb.178-105-182-162.sslip.io/tiles/oikn/building/lod1/v17/tileset.json","heightOffsetM":0} }
```
(values illustrative; the tileset `version` and `url` change after every edit, so always read them from `/catalog`)

# Write API (token)

Headers: `Authorization: Bearer <WRITE_API_TOKEN>` and `X-User: <name>`. Missing token → 401, missing `X-User` → 400, write API off → 503.

| Call | Body / effect | Typical time (KIPP, 389 buildings) |
|---|---|---|
| `POST /features[?rebuild=false]` | `{dataset, theme?, objectid?, footprint:[[lon,lat],…], holes?, height_m, base_z?, attributes?}` → 201 | 11–17 s with rebuild, ~8 s without |
| `PATCH /features/{objectid}[?rebuild=false]` | only the fields that change; attributes merged, `null` removes one | 11–17 s |
| `DELETE /features/{objectid}[?rebuild=false]` | removes the building; the last building of a layer cannot be deleted (409) | ~12 s |
| `POST /layers/{layerId}/rebuild` | rebuild tiles from the database, activate as the next version | ~9 s |
| `PUT /layers/{layerId}/status` | `{"status": "draft"\|"validated"\|"published"\|"archived"}` → `{layerId, previousStatus, status}`. From draft the latest successful import is marked validated first; publishing needs an active tileset; disallowed moves → 409 | < 1 s |

Write response: `{objectid, action, jobId, layerId, tileset: {version, url, buildings} | null, stale}`. Every write adds a row to `catalog.import_job` (who, what, when). Layers and datasets are not created through the API (admin UI or `db/seeds/`); their status is (`PUT /layers/{id}/status`). Error codes: 409 conflict (id exists / last building), 422 invalid geometry, height or attribute name, 502 data saved but tile rebuild failed (call rebuild).

# File upload and download

**Upload** `POST /imports` (token headers as above), `multipart/form-data`: `file`, `dataset`, `theme` (default `building`), `lod` (default 1). The layer must already exist. The format is detected from the content:

| Format | What happens |
|---|---|
| CityJSON 2.0 / CityGML 2.0 | imported as is by citydb-tool (the file must declare its CRS; coordinates are transformed to the database CRS) |
| GeoJSON FeatureCollection | each Polygon (or one-part MultiPolygon, lon/lat) becomes a LOD1 block, same builder and checks as `POST /features`. Properties: `height_m` (required), `base_z` (optional, default = mean ground of the layer), `objectid` (optional, else feature `id`, else generated); all other properties become attributes. Invalid features and ids already in the database are skipped and listed in the job report |

The whole file is one citydb-tool import, one `catalog.import_job` row and (LOD1 layers) one tile rebuild. The call returns at once with `202 {"jobId", "status": "running", "format", "statusUrl"}`; poll `GET /imports/{jobId}` until `status` is `succeeded` or `failed`:

```json
{"jobId": "8b641109-…", "status": "succeeded", "dataset": "zz_scratch", "theme": "building", "lod": 1, "layerId": "c6b04529-…",
 "sourceFile": "KIPP_LOD1.city.json", "sizeBytes": 1218757, "sha256": "66c8…", "counts": {"Building": 389},
 "report": {"format": "cityjson", "tileset": {"version": 1, "url": "/tiles/zz_scratch/building/lod1/v1/tileset.json", "buildings": 389}},
 "startedAt": "…", "finishedAt": "…", "createdBy": "api:claude"}
```
`report` may also hold `skipped` (GeoJSON features not imported, with reason), `tileError` (data saved, call `POST /layers/{id}/rebuild`) and `error` (why the job failed). Max file size 200 MB (413). Unknown format → 422, unknown layer → 404. CityJSON/CityGML ids are not checked against existing objects: uploading the same file twice duplicates the buildings (`/features/{objectid}` then answers 409 unless `?dataset=` is given).

*Why a background job:* gunicorn kills a request after 30 s (`api/entrypoint.sh`), and Caddy, browsers and fetch clients have their own limits; a large file takes minutes. The upload runs in a thread of the API worker and its state lives in `catalog.import_job`, so any worker can answer the poll. Limit: if the API container restarts during an import, that job stays `running`; check the data and re-upload.

**Download** `GET /datasets/{code}/export?format=geojson|cityjson` (no token), `Content-Disposition: attachment`. GeoJSON is built from the database (footprint = ground face of the LOD1 solid, `base_z`, `height_m`, `objectid`, `class`, `lineage`, all generic attributes as flat properties) and can be uploaded again. CityJSON is a citydb-tool export of all layers of the dataset in the model CRS. Both take 1–2 s for the 389 KIPP buildings.

# Tile ↔ database link
Each tile object must carry its `objectid` (gml:id). The frontend reads it on click and calls `/features/{objectid}`. Verified status: **verified on 8 October 2026** with the real KIPP LOD1 tileset (389 buildings, self-hosted, built by `scripts/build_tiles.py`): the tile exposes `objectid` (e.g. `KIPP_0287`) and `/features/KIPP_0287` returns its attributes. Tiles from other converters (e.g. convertwin) still need this check.

Self-hosted tiles are served at `/tiles/…` (same CORS rule as `/api/`). The database stores their address as a relative path and the API returns an absolute URL for the requesting host.

# Connector deliverables
- `client/naraga-catalog.js` - ES module, no dependencies: `listLayers({lod})`, `loadTileset(viewer, lod)`, `getFeature(id)`, `enablePicking(viewer)`.
- `viewer/index.html` - simple Cesium viewer served at `/viewer/`: API URL + ion token inputs (token only in browser localStorage), LOD/theme/dataset selectors, layer metadata (status, version, stale), click → attributes, error banner.
- `postman/` - `naraga-catalog.postman_collection.json`, `local` and `railway` environments (`baseUrl`, secret `ionToken`). Tests per request: status code, schema, filter respected, pagination, error format, response-time budget. Run headless: `newman run postman/naraga-catalog.postman_collection.json -e postman/local.postman_environment.json`.

Cesium ion token lives only in the frontend. NARAGA token auth is a later phase.

# Verification status

On 15 October 2026 (local stack, fresh database): 19 pytest tests including upload/export (`api/tests/test_imports.py`); `KIPP_LOD1.city.json` uploaded through `POST /imports` into `oikn` and into a scratch layer, 389 buildings each, new tileset served (about 5 s per upload); `GET /datasets/oikn/export` returns 389 features in both formats.

On 15 October 2026 on the Hetzner server: 14 pytest tests (local stack, same code), OpenAPI checked (descriptions on all 12 operations, bearer scheme on the 4 write operations), the Postman collection against the live URL, and the CRUD run in `docs/LAPORAN_INPUT_DATA_KIPP.md`. Click-to-attributes in `/viewer/` works with the self-hosted tiles.

Earlier: 
Verified on 4 October 2026 against the local stack: 13 pytest integration tests (filters, pagination, error format, tileset, features, admin audit user) and the Postman collection through newman (21 requests, 61 assertions, 0 failed). The viewer loads, lists layers from the API and shows a clear message for a layer without an active tileset. Loading real tiles and click-to-attributes is not yet tested because no tileset exists yet.
