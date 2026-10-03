# Introduction

Status: **draft v0.1 for review**. Items marked *verify* are assumptions to check before relying on them.


# Configuration (env vars)
| Var | Meaning |
|---|---|
| `DATABASE_URL` / `POSTGRES_*` | DB connection (API and migrations) |
| `CITYDB_SRID`, `CITYDB_HEIGHT_EPSG` | Fixed at the **first** start of the db volume (e.g. 32750 / 4979) |
| `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` | Django |
| `ALLOWED_ORIGINS` | CORS allow-list for the frontend |
| `PROJECT` | Seed folder name under `db/seeds/` |
`.env.example` is committed; `.env` is git-ignored.

# Local
`make up` (db + api) → `make migrate` → `make seed` → open `http://localhost:8000/admin`, `/viewer/`, `/api/v1/docs`. `make test` runs migration + invariant tests. Pinned image: `3dcitydb/3dcitydb-pg:16-3.4-5.1.4`; citydb-tool 1.4.0.

# Railway
Services (same Dockerfiles as local, `railway.toml` per service):
1. **db** - image `3dcitydb/3dcitydb-pg:16-3.4-5.1.4`, volume mounted at the Postgres data dir, private networking only, env `SRID`, `HEIGHT_EPSG`, `POSTGRES_PASSWORD`. *verify:* init works on a Railway volume (may need `PGDATA` subdirectory), volume size/plan cost for the LOD1 data, image runs on Railway's amd64 hosts.
2. **api** - Django; pre-deploy command `dbmate up && psql -f db/seeds/$PROJECT/*.sql`; healthcheck `/api/v1/health`; public domain; `DATABASE_URL` from the private network.
3. **backup** (cron) - nightly `pg_dump -Fc` to an S3-compatible bucket. Do not rely on volume snapshots alone.
4. citydb-tool imports run as a one-off container (local or Railway) over the private network or a temporary TCP proxy; close the proxy afterwards.

Steps: create project → add db service (volume) → wait healthy → add api service from the GitHub repo → set env → deploy → create first admin user (`python manage.py createsuperuser` via Railway shell) → run `newman` smoke test against the Railway environment.
Rollback: redeploy the previous api deployment; DB changes are forward-only migrations (use `dbmate down` only for the latest, before data depends on it); data: restore from dump.

# New project from the template
Copy/fork the repo → change `.env` (SRID, project name) → add `db/seeds/<project>/` → `make up migrate seed`. Add themes by inserting into `ref_theme` (admin UI).
