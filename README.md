# NARAGA 3D Catalog

A reusable catalog and serving stack for 3D city models. It stores CityGML/CityJSON data in **3DCityDB v5 (PostGIS)**, keeps a **catalog** of datasets, layers and 3D Tiles builds, and exposes a **REST API** (read endpoints are public; create/update/delete of buildings need a write token) plus a self-hosted **3D Tiles** endpoint for a Cesium frontend. Non-IT staff manage the catalog through a Django admin.

**Current data:** dataset `oikn`, theme `building`, LOD1 — 389 buildings (KIPP), published, self-hosted tileset (the newest version is always the active one).

---

## Contents

1. [Architecture](#1-architecture)
2. [Quick start](#2-quick-start)
3. [Configuration](#3-configuration)
4. [For frontend developers](#4-for-frontend-developers)
5. [API reference](#5-api-reference)
6. [Data operations (import, tiles, update, delete)](#6-data-operations)
7. [For backend developers](#7-for-backend-developers)
8. [Deployment](#8-deployment)
9. [Repository layout](#9-repository-layout)
10. [Known limitations and open items](#10-known-limitations-and-open-items)
11. [Troubleshooting](#11-troubleshooting)
12. [Further documentation](#12-further-documentation)

---

## 1. Architecture

```
 source file ──import.sh──► 3DCityDB  (schema citydb)      geometry + attributes
 (.city.json / .gml)              │
                                  │  catalog.* (datasets, layers, import jobs, tilesets)
 OBJ ──build_tiles.py──► tiles/…/vN/tileset.json + .glb
                                  │   publish_tiles.sh registers the build in catalog.tileset
                                  ▼
   Frontend ── GET /api/v1/catalog ──► API ──► catalog.v_layer ──► layer + tileset.url
   Frontend ── Cesium3DTileset.fromUrl(url) ──► GET /tiles/…       (served by the API)
   Frontend ── click a building ──► GET /api/v1/features/{objectid} ──► attributes from citydb
```

Key design points:

- **The database does not store tiles.** It stores the model (`citydb.*`) and a *pointer* to each tile build (`catalog.tileset`). Tiles are produced by a separate step and served as static files (or by Cesium ion).
- **Tile ↔ database link.** Every building inside a tile carries its `objectid` (the CityGML `gml:id`, e.g. `KIPP_0287`) as a feature property. The frontend reads it on click and calls `/features/{objectid}`.
- **Layer lifecycle is enforced by the database:** `draft → validated → published → archived`. A layer cannot be `published` without exactly one *active, ready* tileset, and a layer cannot be `validated` unless an import job succeeded and passed validation.
- **Versioned tiles.** Each rebuild is a new version (`v1`, `v2`, …). Activating a new version retires the old one in one transaction, so there is no downtime.
- **Traceability.** Every import is an `import_job` row (file name, SHA-256, size, user, counts). Every change to catalog tables is audited with the signed-in user's name.

| Component | Technology |
|---|---|
| Database | PostgreSQL 16 + PostGIS, 3DCityDB 5.1.4 (`3dcitydb/3dcitydb-pg:16-3.4-5.1.4`) |
| Importer | `citydb-tool` 1.4.0 (Docker) |
| API + admin | Django 5 + django-ninja, gunicorn |
| Migrations | dbmate (`db/migrations/`) |
| Frontend client | Dependency-free ES module (`client/naraga-catalog.js`) + CesiumJS 1.130 |

## 2. Quick start

**Prerequisites:** Docker Desktop (running), `psql` optional. For the tile scripts: Python 3.10+ with `numpy`, `pyproj`, `pygltflib`.

```bash
cp .env.example .env        # then change the passwords
make up                     # builds and starts db + api (migrations, roles, seeds run automatically)
curl localhost:8000/api/v1/health
```

| What | Where |
|---|---|
| Viewer | <http://localhost:8000/viewer/> |
| Admin | <http://localhost:8000/admin/> (user and password from `.env`) |
| Interactive API docs (OpenAPI) | <http://localhost:8000/api/v1/docs> |
| Tiles | `http://localhost:8000/tiles/…` |
| Postgres | `localhost:55432`, user `postgres` |

```bash
make down      # stop (data stays in the pgdata volume)
make psql      # SQL shell
make test      # database invariants + API tests
```

> **Never run `docker compose down -v`.** It deletes the database volume. The coordinate system (`CITYDB_SRID`) is fixed when the volume is first created.

Then open the viewer, choose `oikn` → `building` → `1` → **Load layer**, and click a building.

## 3. Configuration

All settings come from environment variables (`.env` locally, service variables on Railway). `.env.example` is the template.

| Variable | Purpose |
|---|---|
| `POSTGRES_PASSWORD`, `DATABASE_URL` | Database owner connection (migrations, imports) |
| `CITYDB_SRID`, `CITYDB_HEIGHT_EPSG` | Model CRS, e.g. `32750` / `4979`. **Fixed at first start of the volume.** |
| `APP_DB_PASSWORD`, `API_DB_PASSWORD` | Passwords of the login roles `twin_app` (admin) and `twin_api` (read-only API) |
| `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` | Django |
| `ALLOWED_ORIGINS` | Comma-separated origins allowed to call `/api/` **and `/tiles/`** from a browser (CORS). Same-origin needs nothing. |
| `WRITE_API_TOKEN` | Secret for the write endpoints (`Authorization: Bearer …`). **Empty = write API disabled** (returns 503). Use a long random string. |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | First admin account (created on start) |
| `ADMIN_EDIT_ENABLED` | `0` = read-only admin, `1` = editing enabled |
| `PROJECT` | Seed folder under `db/seeds/` (default `oikn`) |

## 4. For frontend developers

You only need the HTTP API. No database access, no token for the API (a Cesium ion token is needed only for ion-hosted tilesets or ion base maps).

### 4.1 Load a layer

```js
import { createClient } from "/client/naraga-catalog.js";   // or copy the file into your app

const client = createClient("http://localhost:8000");        // API origin
const { layer, tileset } = await client.loadTileset(viewer, { dataset: "oikn", theme: "building", lod: 1 });
viewer.zoomTo(tileset);

// click a building -> attributes from the database
client.enablePicking(viewer, (feature, error) => console.log(feature ?? error));
```

### 4.2 Without the client library

```js
const res = await fetch("http://localhost:8000/api/v1/catalog?dataset=oikn&theme=building&lod=1");
const { items } = await res.json();
const t = items[0].tileset;                                   // null if the layer has no active tileset
const tileset = t.provider === "cesium_ion"
  ? await Cesium.Cesium3DTileset.fromIonAssetId(t.ionAssetId)
  : await Cesium.Cesium3DTileset.fromUrl(t.url);              // absolute URL, ready to use
viewer.scene.primitives.add(tileset);

// on click:
const picked = viewer.scene.pick(click.position);
const objectId = picked?.getProperty?.("objectid");           // e.g. "KIPP_0287"
const attrs = await (await fetch(`http://localhost:8000/api/v1/features/${objectId}`)).json();
```

### 4.3 Things to handle

- **`tileset` can be `null`** (layer not published yet). The default `/catalog` call returns only `published` layers; use `status=all` for everything.
- **`isStale: true`** means data changed in the database after the tiles were built. Show a warning; tiles need a rebuild.
- **`heightOffsetM`** is a vertical shift in metres. The client applies it automatically; apply it yourself if you load the tileset manually.
- **Vertical datum** of the current data is `unknown` (see [open items](#10-known-limitations-and-open-items)). Buildings may appear to float or sink relative to terrain until it is resolved.
- **CORS:** if your app runs on another origin, add it to `ALLOWED_ORIGINS`.
- **Errors** share one format: `{"error": {"code", "message", "details"}}`.

## 5. API reference

Base path `/api/v1`, JSON. Read endpoints are open; endpoints marked *token* need the write token (section 5.1). Live OpenAPI: `/api/v1/openapi.json` and `/api/v1/docs`. A Postman collection is in `postman/`.

| Endpoint | Description |
|---|---|
| `GET /health` | Service and database status |
| `GET /catalog` | Layers with their active tileset. Filters: `dataset`, `theme`, `lod` (0–4), `bbox` (`minlon,minlat,maxlon,maxlat`, EPSG:4326), `status` (default `published`, or `all`), `limit` (≤500, default 50), `offset` |
| `GET /layers/{layerId}/tileset` | Active tileset of one layer (404 if none) |
| `GET /datasets`, `GET /datasets/{code}` | Datasets and their layers |
| `GET /datasets/{code}/stats` | Object counts per class and LOD |
| `GET /features/{objectid}` | Attributes of one city object (class, dataset, lineage, LODs available, attributes) |
| `GET /features` | Search. Filters: `dataset`, `bbox`, `q` (name), `limit`, `offset`. Returns GeoJSON of envelope footprints |
| `POST /features` *(token)* | Create a LOD1 building |
| `PATCH /features/{objectid}` *(token)* | Update attributes and/or geometry |
| `DELETE /features/{objectid}` *(token)* | Delete a building |
| `POST /layers/{layerId}/rebuild` *(token)* | Rebuild the tileset from the current database |
| `GET /tiles/{path}` | Static tile files (`tileset.json`, `.glb`). Not part of `/api/v1`. |

Example `/catalog` item:

```json
{
  "layerId": "ec5f6a15-5850-4f44-903c-dab494370892",
  "title": "OIKN buildings LOD1",
  "dataset": {"code": "oikn", "name": "OIKN", "generatedBy": "external", "attribution": null, "verticalDatum": "unknown"},
  "theme": "building", "lod": 1, "status": "published", "featureCount": 389, "isStale": false,
  "bbox": [116.696, -0.987, 116.722, -0.958],
  "tileset": {"id": "…", "version": 1, "provider": "self_hosted", "ionAssetId": null,
              "url": "http://localhost:8000/tiles/oikn/building/lod1/v1/tileset.json",
              "heightOffsetM": 0.0, "publishedAt": "2026-10-08T21:39:06+00:00"}
}
```

`GET /features/{objectid}` also returns `geometry` for LOD1 buildings: `{footprint: [[[lon,lat],…], holes…], baseZ, heightM}`.

The database stores self-hosted tileset URLs as relative paths (`/tiles/…`); the API returns them as absolute URLs for the host that answered the request, so moving hosts needs no data change.

### 5.1 Write API (create, update, delete)

Every write changes the database **and** the visualization in one request: the building is written to 3DCityDB, the layer's tiles are rebuilt from the database, the new build becomes the active tileset version, and an `import_job` row records who did what. After the response, `GET /catalog` already returns the new tileset and a reloaded frontend shows the change.

**Authentication.** Send `Authorization: Bearer <WRITE_API_TOKEN>` and `X-User: <your name>` (the name is stamped on the change and shown in the audit trail as `api:<name>`). Without the token: `401`; token not configured on the server: `503`; missing `X-User`: `400`.

```bash
TOKEN=...   # value of WRITE_API_TOKEN
H=(-H "Authorization: Bearer $TOKEN" -H "X-User: fairuz" -H "Content-Type: application/json")

# Create: footprint = outer ring of [lon, lat]; optional "holes"; height in metres; free-form attributes
curl -X POST localhost:8000/api/v1/features "${H[@]}" -d '{
  "dataset": "oikn", "objectid": "MY_001",
  "footprint": [[116.7090,-0.9720],[116.7094,-0.9720],[116.7094,-0.9717],[116.7090,-0.9717]],
  "height_m": 25, "attributes": {"nama": "Gedung A", "lantai": 6}}'

# Read (no token)
curl localhost:8000/api/v1/features/MY_001

# Update: send only what changes; attributes are merged, a null value removes an attribute
curl -X PATCH localhost:8000/api/v1/features/MY_001 "${H[@]}" -d '{"height_m": 40, "attributes": {"nama": "Gedung A (baru)", "lantai": null}}'

# Delete
curl -X DELETE localhost:8000/api/v1/features/MY_001 "${H[@]}"
```

Response (create/update/delete): `{"objectid", "action", "jobId", "layerId", "tileset": {"version", "url", "buildings"}, "stale": false}`.

Rules and behaviour:

- `objectid` is generated (`API_xxxxxxxxxx`) when omitted; it must be unique (`409` otherwise). Allowed characters: letters, digits, `_ . -`.
- `base_z` (height of the ground in the model's vertical reference) defaults to the mean ground level of the layer, so new buildings sit with their neighbours. Pass it explicitly if you know it.
- Invalid footprints (self-intersecting, area < 1 m², bad coordinates) and heights outside 0–1000 m return `422`.
- A layer must keep at least one building (`409` when deleting the last one).
- `?rebuild=false` skips the tile rebuild for bulk edits; the layer then shows `isStale: true` until you call `POST /layers/{layerId}/rebuild`.
- If the data was saved but the tile rebuild failed, the response is `502`; call the rebuild endpoint to recover.
- Writes are serialized (one at a time, database advisory lock). Each write rebuilds the whole tileset (about one second for 400 buildings); for hundreds of edits use `?rebuild=false` and rebuild once.
- Only the **two newest** tile folders per layer are kept on disk; older tileset versions stay in the catalog as `retired`.
- **Limits.** Edits are block models (extruded footprint) at LOD1. Updating or creating a building stores a single `lod1Solid`; thematic surface objects (Wall/Roof/Ground) that came with an imported file are not recreated. Buildings whose ground is not exactly one polygon cannot be PATCHed without sending both `footprint` and `height_m`; in the KIPP data all 389 buildings qualify.
- Update is *delete + re-import* of that one building. If the re-import fails, the previous block is restored automatically.

End-to-end check (creates, reads, updates and deletes a test building and compares the database with the ids inside the served tiles after every step):

```bash
python3 scripts/crud_smoke.py        # 23 checks; reads WRITE_API_TOKEN from .env
```

## 6. Data operations

Two ways to change data. For **a few buildings**, use the write API (section 5.1): it updates the database and the tiles together. For **bulk loads or replacing a whole layer**, use the scripts below: **database first, then tiles, then register the new tileset version.**

Scripts (all run from the repository root, local Docker stack):

| Script | Purpose |
|---|---|
| `scripts/import.sh` | Import a CityGML 2.0 (`.gml`/`.xml`) or CityJSON 2.0 (`.json`) file with an import job and lineage tag `<dataset>.<theme>.lod<N>` |
| `scripts/build_tiles.py` | Build a 3D Tiles 1.1 tileset from an OBJ, storing each building's `objectid` |
| `scripts/publish_tiles.sh` | Register a built tileset version and make it the active one (retires the old one atomically). `DRY=1` checks everything and rolls back |

### 6.1 First-time load of a new layer

```bash
# 1. Import (creates the draft layer and an import job)
scripts/import.sh USER=yourname FILE=data/KIPP_LOD1.city.json DATASET=oikn THEME=building LOD=1

# 2. Validate the source geometry (val3dity recommended), then mark the job and layer.
#    SQL is in docs/en/07_RUNBOOK.md ("Validate and mark the layer validated").

# 3. Build tiles. Object names in the OBJ ("o KIPP_0000") MUST equal the objectid/gml:id in the database.
python3 scripts/build_tiles.py data/KIPP_LOD1.obj tiles/oikn/building/lod1/v1

# 4. Register and publish
scripts/publish_tiles.sh USER=yourname DATASET=oikn THEME=building LOD=1 VERSION=1
```

The OBJ header carries the offset to the model CRS (`# offset X=… Y=… Z=…`). `build_tiles.py` reads it, reprojects to ECEF and writes a single-tile tileset (suitable for a few thousand buildings; see [limitations](#10-known-limitations-and-open-items)).

### 6.2 Replace all data of a layer

```bash
NET=$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' $(docker compose ps -q db))

# a. remove the old objects by lineage
docker run --rm --platform linux/amd64 --network "$NET" 3dcitydb/citydb-tool:1.4.0 delete \
  -H db -d postgres -u postgres -p "$POSTGRES_PASSWORD" --delete-mode=delete -f "lineage = 'oikn.building.lod1'"

# b. import the new file, c. build tiles as the next version, d. publish it
scripts/import.sh USER=yourname FILE=data/new/KIPP_LOD1.city.json DATASET=oikn THEME=building LOD=1
python3 scripts/build_tiles.py data/new/KIPP_LOD1.obj tiles/oikn/building/lod1/v2
scripts/publish_tiles.sh USER=yourname DATASET=oikn THEME=building LOD=1 VERSION=2
```

A published layer stays published during a re-import and shows `isStale: true` until the new tileset is activated.

### 6.3 Add buildings

Import the additional file with `MODE=skip` (objects whose id already exists are skipped), then rebuild tiles from the **complete** OBJ and publish the next version. A tileset is always a complete build; it is not patched.

```bash
scripts/import.sh USER=yourname FILE=data/extra.city.json DATASET=oikn THEME=building LOD=1 MODE=skip
```

### 6.4 Delete buildings or a layer

```bash
# specific buildings
docker run --rm --platform linux/amd64 --network "$NET" 3dcitydb/citydb-tool:1.4.0 delete \
  -H db -d postgres -u postgres -p "$POSTGRES_PASSWORD" --delete-mode=delete -f "objectid = 'KIPP_0287'"
```

Then rebuild tiles without those buildings and publish the next version. To retire a whole layer, set it to `archived` in the admin (its tilesets are retired automatically); delete the objects by lineage if the data must go as well.

> `MODE=skip` and delete-by-`objectid` have **not been tested** in this repository yet. Delete-by-lineage and full import are tested. Try them on a small file first and compare `/datasets/{code}/stats`.

### 6.5 Adjust height

If buildings float or sink, register a new version with an offset (no tile rebuild needed), or edit `height_offset_m` in the admin:

```bash
scripts/publish_tiles.sh USER=yourname DATASET=oikn THEME=building LOD=1 VERSION=3 HEIGHT_OFFSET=-25
```

### 6.6 Verify after any change

```bash
curl -s localhost:8000/api/v1/datasets/oikn/stats
curl -s "localhost:8000/api/v1/catalog?dataset=oikn&lod=1" | python3 -m json.tool   # status, isStale, tileset
```

Then load the layer in the viewer and click a building.

### 6.7 Cesium ion instead of self-hosting

Register a tileset with provider `cesium_ion` and an `ion_asset_id` through the admin; the same API and client work unchanged. See the runbook ("Publish a tileset").

## 7. For backend developers

- **Schema changes** go through dbmate migrations in `db/migrations/` (forward-only; `dbmate down` only for the latest migration before data depends on it). The API container runs `dbmate up` on every start.
- **Business rules live in the database** (`db/migrations/…_business_rules.sql`): status transitions, "published needs an active ready tileset", one active tileset per layer, audit stamping. Add rules there, not only in Python. Checks are in `db/tests/invariants.sql`.
- **Database roles:** `twin_api` is read-only and used by the public API; `twin_app` is used by the admin. Every admin write runs in a transaction with `app.user` set; writes with an empty user are rejected.
- **API code:** `api/catalog/api.py` (endpoints), `api/catalog/edit.py` (write path: CityGML builder, citydb-tool calls, catalog bookkeeping, tile sync), `api/catalog/tiles.py` (3D Tiles writer shared with `scripts/build_tiles.py`), `api/catalog/admin.py` (admin), `api/catalog/middleware.py` (CORS, audit user), `api/config/urls.py` (routes, including `/tiles/` and `/viewer/`).
- **Write path internals.** The API image contains a JRE and `citydb-tool`; writes build a one-building CityGML file and import/delete it with the same tool as `scripts/import.sh`, so stored data has the same structure as imported data. The subprocess uses the owner connection from `DATABASE_URL`; the public read path still uses the SELECT-only role `twin_api`. Catalog changes run as `twin_app` with `app.user = api:<X-User>`. Treat `WRITE_API_TOKEN` like a database password.
- **Tests:** `docker compose exec api python -m pytest -q tests` (14 tests, isolated dataset `zz_test`), `python3 scripts/crud_smoke.py` (write API + tile sync end to end). `db/tests/invariants.sql` must run on a **scratch/empty** database; on a database that already has tilesets it fails (`make test` runs it first). Postman: `newman run postman/naraga-catalog.postman_collection.json -e postman/local.postman_environment.json`. Do not run the tests against a production database.
- **Adding a project:** copy the repository, set `.env` (SRID, `PROJECT`), add `db/seeds/<project>/`. Add themes via `ref_theme` in the admin.
- **Tile format:** `build_tiles.py` writes glTF with `EXT_mesh_features` and `EXT_structural_metadata` (property `objectid`, STRING). Any other converter is acceptable if each building exposes `objectid` (the client also tries `gml_id`, `gmlId`, `id`, `name`).

## 8. Deployment

Step-by-step Railway procedure: [`docs/en/08_RAILWAY_GUIDE.md`](docs/en/08_RAILWAY_GUIDE.md). Overview: [`docs/en/06_DEPLOYMENT.md`](docs/en/06_DEPLOYMENT.md).

Self-hosted tiles need persistent, **writable** storage in production (the write API writes new tile versions there). Options: mount a Railway Volume at `/srv/tiles`; add `COPY tiles tiles` to `api/Dockerfile` (the current tileset is about 4 MB); or use Cesium ion. The `tiles/` folder is mounted read-write from the host in `docker-compose.yml` for local use and is not baked into the image. The API image also carries a JRE and `citydb-tool` (about 300 MB larger than a plain Python image).

Backups: `deploy/backup/backup.sh` (nightly `pg_dump -Fc`, optional S3 upload). Tiles are reproducible from source files; keep the source files.

## 9. Repository layout

```
api/            Django API + admin (api/catalog), Dockerfile, entrypoint
client/         naraga-catalog.js — frontend connector
viewer/         Reference Cesium viewer served at /viewer/
db/migrations/  dbmate SQL migrations        db/seeds/<project>/  project seed data
db/tests/       database invariant checks
scripts/        import.sh, build_tiles.py, publish_tiles.sh
tiles/          Built 3D Tiles (git-ignored locally; tiles/<dataset>/<theme>/lod<N>/v<version>/)
data/           Source data (not committed)
deploy/         Backup job and db image for Railway
postman/        API collection and environments
docs/en/        Design documents, runbook, Railway guide      docs/pdf/  rendered PDFs
```

## 10. Known limitations and open items

- **Geometry validation:** `val3dity` could not be installed on the development machine. The KIPP LOD1 import job was marked as passed based on a custom check (all 389 solids closed, each edge shared by exactly two faces), recorded in the job report. Run `val3dity` and update the report for an official result.
- **Vertical datum:** dataset `oikn` has `vertical_datum = unknown` and no attribution. The KIPP heights (ground ≈ 29 m) have no confirmed reference (ellipsoid vs. geoid). The tileset is registered with `heightOffsetM = 0`.
- **Single-tile tileset:** `build_tiles.py` writes one tile (about 4 MB for 389 buildings). For tens of thousands of buildings use a hierarchical tiler.
- **LOD1 only.** LOD2 (NARAGA) and LOD4 layers exist as drafts without data.
- **Untested operations:** `MODE=skip` and delete-by-`objectid` (see section 6).
- **Self-hosted tile storage in production** is not set up yet (section 8).
- Admin is read-only by default (`ADMIN_EDIT_ENABLED=0`).
- **Write API** is token-based (one shared secret plus a free-text `X-User`), not per-user authentication. Put it behind your own gateway/SSO before exposing it publicly. See the limits in section 5.1.
- `db/tests/invariants.sql` only works on an empty scratch database.

## 11. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `Cannot connect to the Docker daemon` | Start Docker Desktop, then `docker compose up -d`. |
| Viewer shows "no active tileset" | Layer is not `published` or its tileset is not `ready`/active. Check `/api/v1/catalog?status=all`. |
| Black canvas right after loading | The globe renders a moment later; wait and re-check. Open the browser console for tile errors. |
| Click on a building says "no id property" | The tiles lack `objectid`. Rebuild with `build_tiles.py`. |
| Click works but `/features/{id}` returns 404 | Object names in the OBJ differ from `gml:id` in the database. They must be identical. |
| Buildings float or sink | Vertical datum mismatch. Register a version with `HEIGHT_OFFSET` (section 6.5). |
| Browser blocks `/api` or `/tiles` | Add the frontend origin to `ALLOWED_ORIGINS` and restart the api service. |
| `isStale` is true | Database changed after the tiles were built. Rebuild and publish the next version. |
| Import fails on CRS | Source CRS must equal `CITYDB_SRID`. Reproject first. |
| Write call returns `503` | `WRITE_API_TOKEN` is empty on the server. Set it and restart the api service. |
| Write call returns `502` | Data saved but tiles not rebuilt. `POST /layers/{layerId}/rebuild`. |
| `Cannot publish: the layer has no active Ready tileset` | Register a tileset first (`publish_tiles.sh`), then publish. |

## 12. Further documentation

Read in this order for the full design: [DDD](docs/en/01_DDD.md) → [ERD](docs/en/02_ERD.md) → [Database schema](docs/en/03_DATABASE_SCHEMA.md) → [SAD](docs/en/04_SAD.md) → [API](docs/en/05_API.md) → [Deployment](docs/en/06_DEPLOYMENT.md) → [Runbook](docs/en/07_RUNBOOK.md) → [Railway guide](docs/en/08_RAILWAY_GUIDE.md). PDFs with rendered diagrams are in `docs/pdf/`; rebuild with `python3 docs/en/build.py` (needs pandoc, Chrome, poppler). An Indonesian operating guide is in [`docs/PANDUAN_SERVE_UPDATE.md`](docs/PANDUAN_SERVE_UPDATE.md).

### For data editors (non-IT)

- Sign in at `/admin` with your own account; never share accounts.
- **Datasets, Layers, Tilesets, Import jobs** show everything that exists and what was uploaded. "History" on a record shows who changed what.
- Every change is recorded with your name. You cannot delete; ask an admin to archive instead.
- Do not edit the `Code` of a dataset after data was loaded (the system refuses it).
- Bounding boxes and object counts are filled automatically; do not type them.
