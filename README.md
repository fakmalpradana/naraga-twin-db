# Digital Twin Catalog (NARAGA 3D Catalog) — template

A reusable database and catalog for 3D city models (3DCityDB v5 + PostGIS), with an API for a Cesium frontend and an admin web page for non-IT users.

**Status: design phase.** Read in this order: [DDD](docs/en/01_DDD.md) → [ERD](docs/en/02_ERD.md) → [Database schema](docs/en/03_DATABASE_SCHEMA.md) → [SAD](docs/en/04_SAD.md) → [API](docs/en/05_API.md) → [Deployment](docs/en/06_DEPLOYMENT.md) → [Runbook](docs/en/07_RUNBOOK.md). Readable PDFs with rendered diagrams are in `docs/pdf/`; rebuild with `python3 docs/en/build.py` (needs pandoc, Chrome, poppler)..

## For data editors (non-IT)
- Sign in at `/admin` with your own account (never share accounts).
- **Datasets / Layers / Tilesets / Import jobs** show everything that exists and what was uploaded. Use filters and search; "History" on a record shows who changed what.
- Every change is recorded with your name. You cannot delete; ask an admin to archive instead.
- Do not edit `Code` of a dataset after data was loaded (the system will refuse).
- Bounding boxes and object counts are filled automatically; do not type them.

## Layout
`docs/en/` Markdown sources, `docs/pdf/` PDFs · `db/migrations/` SQL (dbmate) · `db/seeds/<project>/` project data · `db/tests/` checks · (later) `api/`, `viewer/`, `client/`, `postman/`.

## Test the database design now
```bash
docker run -d --name twindb-scratch -e POSTGRES_PASSWORD=scratch -e SRID=32750 -e HEIGHT_EPSG=4979 -p 55432:5432 3dcitydb/3dcitydb-pg:16-3.4-5.1.4
dbmate --url "postgres://postgres:scratch@localhost:55432/postgres?sslmode=disable" up
psql "postgres://postgres:scratch@localhost:55432/postgres" -v ON_ERROR_STOP=1 -f db/tests/invariants.sql
```
