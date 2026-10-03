# Introduction

Status: **draft v0.1**. Commands marked *to implement* are created during the build (Makefile, wrapper script).


# Import a CityGML file *(to implement: `make import`)*
1. Pre-check: unique `gml:id`s (UUID or dataset prefix); source CRS equals database SRID (else reproject first); note vertical datum.
2. Create the `import_job` (done by the wrapper): file name, sha256, size, user.
3. Run `citydb import citygml --lineage <dataset>.<theme>.lod<n> --reason-for-update import_job:<id> --updating-person <user> --import-mode import_all <file>` (verify flags with `citydb import citygml --help`).
4. Validate: counts equal source; val3dity report stored in `import_job.report`; set `validation_passed`, `status='succeeded'`.
5. `SELECT catalog.refresh_derived();` then link `import_job_layer`, set `layer.validated_by_job_id` and status `validated`.

# Re-import a layer
Delete first: `citydb delete --delete-mode=delete --sql-filter "<lineage = '<tag>'>"`, then import again. Re-import of a published layer keeps it published but shows **stale** until tiles are rebuilt.

# Publish
Convert (convertwin) → upload to Cesium ion → insert `tileset` (`provider=cesium_ion`, `ion_asset_id`, `source_snapshot_at`, `height_offset_m`, `status=ready`) → set `is_active` → layer `published`. Switching versions: see database-schema §6.

# Backup / restore
Nightly `pg_dump -Fc`. Restore test (before first import and monthly): start a fresh 3DCityDB container with the same `SRID`, `pg_restore --clean --if-exists -d postgres dump`, run `db/tests/invariants.sql`.

# Rotate Cesium ion token
Create a new read-only token for the used assets in Cesium ion → give it to the frontend config → revoke the old one. The API stores no token.

# Upgrade 3DCityDB *(stub)*
Pin new image tag in staging → run `citydb` upgrade per official docs → `make migrate` (re-creates `feature_lineage_inx` if missing) → run tests → promote.

# Add a user (non-IT editor)
Admin UI → Users → add (username, password) → assign group `viewer`, `editor` or `admin`. One account per person.
