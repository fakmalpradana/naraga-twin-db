# Introduction

This document is the reference for every table, view, column, constraint, index, trigger, function and role added by the catalog migrations. It is generated from the live database catalogue (PostgreSQL 16.4, PostGIS 3.4.3, 3DCityDB 5.1.4). The 3DCityDB schema `citydb` is documented by its project and is not repeated here.

# Conventions

| Topic | Convention |
|---|---|
| Migrations | Plain SQL files in `db/migrations`, applied by dbmate in order. Applied files are never edited. |
| Keys | `uuid` primary keys (`gen_random_uuid()`); reference tables use a short text `code`. |
| Names and time | snake_case names, `timestamptz` for every time. |
| Coordinates | `bbox` is `geometry(Polygon, 4326)`, computed from the data, never typed. The source CRS is only recorded in `dataset.crs_epsg`; the database SRID is fixed at creation. |
| LOD | 0 to 4 (CityGML 2.0). LOD4 of CityGML 2.0 is stored by 3DCityDB as deprecated properties; layers still use `lod = 4`. |
| Lineage tag | `dataset.theme.lodN` in `citydb.feature.lineage` links 3D objects to a layer. |
| Code lists | Reference tables, not ENUM types. Add a row to extend; no migration needed. |
| Audit columns | `created_at`, `created_by`, `updated_at`, `updated_by` on `dataset`, `import_job`, `layer`, `tileset`. Filled by trigger from the signed-in user. |
| Mandatory user | Set `app.user` per transaction: `SELECT set_config('app.user', 'name', true);`. Without it every write is rejected. |
| Deletion | Archive through status. DELETE is allowed to `twin_admin` only. Old rows stay in `audit.change_log`. |
| Comments | Every table and column has a plain-language `COMMENT`, shown in the admin UI. |

# Schemas

| Schema | Purpose | Tables | Views |
|---|---|---:|---:|
| `catalog` | Reference lists, datasets, layers, tilesets and import history. Editable by trained staff through the admin UI. | 10 | 2 |
| `audit` | Automatic history of every change to catalog data. | 1 | 0 |

Other schemas: `citydb` (3DCityDB v5, untouched except the index `feature_lineage_inx`) and `app` (Django tables).

# Schema catalog

Reference lists, datasets, layers, tilesets and import history. Editable by trained staff through the admin UI.

## catalog.v_layer

*view.* One row per layer with its active tileset, object count and stale flag. Used by the API and the inventory.

| Column | Type | Null | Default | Meaning |
|---|---|---|---|---|
| `layer_id` | `uuid` | yes | - | - |
| `dataset_id` | `uuid` | yes | - | - |
| `dataset_code` | `text` | yes | - | - |
| `dataset_name` | `text` | yes | - | - |
| `generated_by` | `text` | yes | - | - |
| `attribution` | `text` | yes | - | - |
| `vertical_datum` | `text` | yes | - | - |
| `region` | `text` | yes | - | - |
| `theme_code` | `text` | yes | - | - |
| `lod` | `smallint` | yes | - | - |
| `title` | `text` | yes | - | - |
| `status` | `text` | yes | - | - |
| `bbox_minx` | `double precision` | yes | - | - |
| `bbox_miny` | `double precision` | yes | - | - |
| `bbox_maxx` | `double precision` | yes | - | - |
| `bbox_maxy` | `double precision` | yes | - | - |
| `bbox` | `geometry(Polygon,4326)` | yes | - | - |
| `feature_count` | `bigint` | yes | - | - |
| `tileset_id` | `uuid` | yes | - | - |
| `tileset_version` | `integer` | yes | - | - |
| `provider` | `text` | yes | - | - |
| `ion_asset_id` | `bigint` | yes | - | - |
| `url` | `text` | yes | - | - |
| `height_offset_m` | `numeric` | yes | - | - |
| `published_at` | `timestamp with time zone` | yes | - | - |
| `source_snapshot_at` | `timestamp with time zone` | yes | - | - |
| `is_stale` | `boolean` | yes | - | - |
| `updated_at` | `timestamp with time zone` | yes | - | - |
| `updated_by` | `text` | yes | - | - |

## catalog.dataset

*table.* A collection of data for one area from one source, e.g. OIKN.

| Column | Type | Null | Default | Meaning |
|---|---|---|---|---|
| `id` | `uuid` | no | `gen_random_uuid()` | - |
| `code` | `text` | no | - | Short unique name (a-z, 0-9, _). It is the tag stored on every 3D object, so it cannot be changed once data is imported. |
| `name` | `text` | no | - | - |
| `description` | `text` | yes | - | - |
| `region` | `text` | yes | - | - |
| `source_system` | `text` | yes | - | - |
| `owner_org` | `text` | yes | - | - |
| `contact_email` | `text` | yes | - | - |
| `generated_by` | `text` | no | `'external'::text` | - |
| `crs_epsg` | `integer` | yes | - | Coordinate system of the SOURCE files (record only). The database itself has one fixed coordinate system. |
| `vertical_datum` | `text` | no | `'unknown'::text` | Height reference of the source: ellipsoid, egm2008, egm96, local or unknown. Needed to place tiles correctly in Cesium. |
| `license` | `text` | yes | - | - |
| `attribution` | `text` | yes | - | Credit line shown on the map (copyright/source). |
| `bbox` | `geometry(Polygon,4326)` | yes | - | Computed automatically from the data after import. Do not type by hand. |
| `status` | `text` | no | `'active'::text` | active or archived. Archiving a dataset archives all its layers. |
| `created_at` | `timestamp with time zone` | no | `now()` | - |
| `created_by` | `text` | no | `''::text` | Filled automatically with the signed-in user. |
| `updated_at` | `timestamp with time zone` | no | `now()` | - |
| `updated_by` | `text` | no | `''::text` | - |

Constraints:

- `CHECK ((code ~ '^[a-z0-9_]+$'::text))`
- `CHECK ((vertical_datum = ANY (ARRAY['ellipsoid'::text, 'egm2008'::text, 'egm96'::text, 'local'::text, 'unknown'::text])))`
- `CHECK ((status = ANY (ARRAY['active'::text, 'archived'::text])))`
- `PRIMARY KEY (id)`
- `UNIQUE (code)`
- `FOREIGN KEY (generated_by) REFERENCES catalog.ref_generated_by(code)`

Indexes:

- `CREATE UNIQUE INDEX dataset_code_key USING btree (code)`
- `CREATE INDEX dataset_bbox_gix USING gist (bbox)`

Triggers:

- `a_stamp`: BEFORE INSERT OR UPDATE ON catalog.dataset FOR EACH ROW runs `catalog.stamp()`
- `z_audit`: AFTER INSERT OR DELETE OR UPDATE ON catalog.dataset FOR EACH ROW runs `audit.log_change('id')`
- `b_guard`: BEFORE UPDATE OF code ON catalog.dataset FOR EACH ROW runs `catalog.dataset_guard()`
- `c_archive`: AFTER UPDATE OF status ON catalog.dataset FOR EACH ROW runs `catalog.dataset_archive_cascade()`

## catalog.import_job

*table.* One uploaded/imported file and what happened to it (the upload registry).

| Column | Type | Null | Default | Meaning |
|---|---|---|---|---|
| `id` | `uuid` | no | `gen_random_uuid()` | - |
| `dataset_id` | `uuid` | no | - | - |
| `tool` | `text` | no | `'citydb-tool'::text` | - |
| `tool_version` | `text` | yes | - | - |
| `citygml_version` | `text` | yes | - | - |
| `source_file` | `text` | no | - | - |
| `source_sha256` | `text` | yes | - | Fingerprint of the file, to prove which exact file was imported. |
| `source_size_bytes` | `bigint` | yes | - | - |
| `import_mode` | `text` | no | `'import_all'::text` | - |
| `lineage_tag` | `text` | yes | - | Tag written on imported objects: dataset.theme.lodN. |
| `status` | `text` | no | `'running'::text` | - |
| `validation_passed` | `boolean` | yes | - | True when geometry/count checks (e.g. val3dity) passed. Required before a layer can be validated. |
| `counts` | `jsonb` | no | `'{}'::jsonb` | - |
| `report` | `jsonb` | no | `'{}'::jsonb` | Detailed validation report (JSON). |
| `started_at` | `timestamp with time zone` | no | `now()` | - |
| `finished_at` | `timestamp with time zone` | yes | - | - |
| `created_at` | `timestamp with time zone` | no | `now()` | - |
| `created_by` | `text` | no | `''::text` | - |
| `updated_at` | `timestamp with time zone` | no | `now()` | - |
| `updated_by` | `text` | no | `''::text` | - |

Constraints:

- `CHECK ((source_sha256 ~ '^[0-9a-f]{64}$'::text))`
- `CHECK ((import_mode = ANY (ARRAY['import_all'::text, 'skip'::text, 'delete'::text, 'terminate'::text])))`
- `CHECK ((status = ANY (ARRAY['running'::text, 'succeeded'::text, 'failed'::text])))`
- `PRIMARY KEY (id)`
- `FOREIGN KEY (dataset_id) REFERENCES catalog.dataset(id)`

Indexes:

- `CREATE INDEX import_job_dataset_ix USING btree (dataset_id, started_at DESC)`

Triggers:

- `a_stamp`: BEFORE INSERT OR UPDATE ON catalog.import_job FOR EACH ROW runs `catalog.stamp()`
- `z_audit`: AFTER INSERT OR DELETE OR UPDATE ON catalog.import_job FOR EACH ROW runs `audit.log_change('id')`
- `b_guard`: BEFORE INSERT OR UPDATE ON catalog.import_job FOR EACH ROW runs `catalog.import_job_guard()`

## catalog.import_job_layer

*table.* Which layers a file filled (one file can fill several).

| Column | Type | Null | Default | Meaning |
|---|---|---|---|---|
| `job_id` | `uuid` | no | - | - |
| `layer_id` | `uuid` | no | - | - |

Constraints:

- `PRIMARY KEY (job_id, layer_id)`
- `FOREIGN KEY (job_id) REFERENCES catalog.import_job(id) ON DELETE CASCADE`
- `FOREIGN KEY (layer_id) REFERENCES catalog.layer(id)`

Triggers:

- `z_audit`: AFTER INSERT OR DELETE OR UPDATE ON catalog.import_job_layer FOR EACH ROW runs `audit.log_change('job_id', 'layer_id')`

## catalog.layer

*table.* What the frontend chooses: one theme at one LOD of a dataset (e.g. OIKN buildings LOD1).

| Column | Type | Null | Default | Meaning |
|---|---|---|---|---|
| `id` | `uuid` | no | `gen_random_uuid()` | - |
| `dataset_id` | `uuid` | no | - | - |
| `theme_code` | `text` | no | - | - |
| `lod` | `smallint` | no | - | - |
| `title` | `text` | yes | - | - |
| `description` | `text` | yes | - | - |
| `status` | `text` | no | `'draft'::text` | draft -> validated -> published -> archived. Moves are checked by the database. |
| `validated_by_job_id` | `uuid` | yes | - | The successful import that justified validation. |
| `bbox` | `geometry(Polygon,4326)` | yes | - | Computed automatically after import. |
| `created_at` | `timestamp with time zone` | no | `now()` | - |
| `created_by` | `text` | no | `''::text` | - |
| `updated_at` | `timestamp with time zone` | no | `now()` | - |
| `updated_by` | `text` | no | `''::text` | - |

Constraints:

- `PRIMARY KEY (id)`
- `UNIQUE (dataset_id, theme_code, lod)`
- `FOREIGN KEY (dataset_id) REFERENCES catalog.dataset(id)`
- `FOREIGN KEY (theme_code) REFERENCES catalog.ref_theme(code)`
- `FOREIGN KEY (lod) REFERENCES catalog.ref_lod_level(lod)`
- `FOREIGN KEY (status) REFERENCES catalog.ref_layer_status(code)`
- `FOREIGN KEY (validated_by_job_id) REFERENCES catalog.import_job(id)`

Indexes:

- `CREATE UNIQUE INDEX layer_dataset_id_theme_code_lod_key USING btree (dataset_id, theme_code, lod)`
- `CREATE INDEX layer_lod_status_ix USING btree (lod, status)`
- `CREATE INDEX layer_bbox_gix USING gist (bbox)`

Triggers:

- `a_stamp`: BEFORE INSERT OR UPDATE ON catalog.layer FOR EACH ROW runs `catalog.stamp()`
- `z_audit`: AFTER INSERT OR DELETE OR UPDATE ON catalog.layer FOR EACH ROW runs `audit.log_change('id')`
- `b_guard`: BEFORE UPDATE ON catalog.layer FOR EACH ROW runs `catalog.layer_guard()`
- `c_archive`: AFTER UPDATE OF status ON catalog.layer FOR EACH ROW runs `catalog.layer_archive_cascade()`
- `y_published_needs_tileset`: AFTER INSERT OR UPDATE ON catalog.layer DEFERRABLE INITIALLY DEFERRED FOR EACH ROW runs `catalog.check_published_has_tileset()`

## catalog.ref_generated_by

*table.* Who produced the data: an external provider, NARAGA, or manual work.

| Column | Type | Null | Default | Meaning |
|---|---|---|---|---|
| `code` | `text` | no | - | - |
| `name` | `text` | no | - | - |
| `description` | `text` | yes | - | - |

Constraints:

- `CHECK ((code ~ '^[a-z0-9_]+$'::text))`
- `PRIMARY KEY (code)`

Triggers:

- `z_audit`: AFTER INSERT OR DELETE OR UPDATE ON catalog.ref_generated_by FOR EACH ROW runs `audit.log_change('code')`

## catalog.ref_layer_status

*table.* Lifecycle of a layer. Allowed moves are enforced by the database.

| Column | Type | Null | Default | Meaning |
|---|---|---|---|---|
| `code` | `text` | no | - | - |
| `name` | `text` | no | - | - |
| `description` | `text` | yes | - | - |
| `sort` | `integer` | no | - | - |

Constraints:

- `PRIMARY KEY (code)`

Triggers:

- `z_audit`: AFTER INSERT OR DELETE OR UPDATE ON catalog.ref_layer_status FOR EACH ROW runs `audit.log_change('code')`

## catalog.ref_lod_level

*table.* Level of Detail per CityGML 2.0 (0 = footprint/terrain ... 4 = interior).

| Column | Type | Null | Default | Meaning |
|---|---|---|---|---|
| `lod` | `smallint` | no | - | - |
| `name` | `text` | no | - | - |
| `description` | `text` | yes | - | - |

Constraints:

- `CHECK (((lod >= 0) AND (lod <= 4)))`
- `PRIMARY KEY (lod)`

Triggers:

- `z_audit`: AFTER INSERT OR DELETE OR UPDATE ON catalog.ref_lod_level FOR EACH ROW runs `audit.log_change('lod')`

## catalog.ref_theme

*table.* Subject of a layer: building, bridge, road, terrain ... Add a row to support a new theme.

| Column | Type | Null | Default | Meaning |
|---|---|---|---|---|
| `code` | `text` | no | - | Short lowercase code, used in the lineage tag (dataset.theme.lodN). Do not change after use. |
| `name` | `text` | no | - | - |
| `description` | `text` | yes | - | - |
| `citydb_objectclass_hint` | `text[]` | yes | - | Typical 3DCityDB class names for this theme (documentation only). |
| `sort` | `integer` | no | `100` | - |

Constraints:

- `CHECK ((code ~ '^[a-z0-9_]+$'::text))`
- `PRIMARY KEY (code)`

Triggers:

- `z_audit`: AFTER INSERT OR DELETE OR UPDATE ON catalog.ref_theme FOR EACH ROW runs `audit.log_change('code')`

## catalog.ref_tileset_status

*table.* Lifecycle of one 3D Tiles build.

| Column | Type | Null | Default | Meaning |
|---|---|---|---|---|
| `code` | `text` | no | - | - |
| `name` | `text` | no | - | - |
| `description` | `text` | yes | - | - |
| `sort` | `integer` | no | - | - |

Constraints:

- `PRIMARY KEY (code)`

Triggers:

- `z_audit`: AFTER INSERT OR DELETE OR UPDATE ON catalog.ref_tileset_status FOR EACH ROW runs `audit.log_change('code')`

## catalog.tileset

*table.* One 3D Tiles build of a layer. Only the active one is served.

| Column | Type | Null | Default | Meaning |
|---|---|---|---|---|
| `id` | `uuid` | no | `gen_random_uuid()` | - |
| `layer_id` | `uuid` | no | - | - |
| `version` | `integer` | no | - | - |
| `provider` | `text` | no | - | - |
| `ion_asset_id` | `bigint` | yes | - | Cesium ion asset number (required when provider is cesium_ion). |
| `url` | `text` | yes | - | tileset.json address (required when provider is self_hosted). |
| `converter_name` | `text` | yes | - | - |
| `converter_version` | `text` | yes | - | - |
| `source_snapshot_at` | `timestamp with time zone` | yes | - | Data state used for this build. If the data changed later, the layer shows as stale. |
| `height_offset_m` | `numeric` | no | `0` | Vertical shift (metres) applied so buildings sit on the terrain. |
| `status` | `text` | no | `'building'::text` | - |
| `is_active` | `boolean` | no | `false` | The one build currently served. Only a Ready tileset can be active. |
| `published_at` | `timestamp with time zone` | yes | - | - |
| `created_at` | `timestamp with time zone` | no | `now()` | - |
| `created_by` | `text` | no | `''::text` | - |
| `updated_at` | `timestamp with time zone` | no | `now()` | - |
| `updated_by` | `text` | no | `''::text` | - |

Constraints:

- `CHECK ((version > 0))`
- `CHECK ((provider = ANY (ARRAY['cesium_ion'::text, 'self_hosted'::text])))`
- `CHECK ((((provider = 'cesium_ion'::text) AND (ion_asset_id IS NOT NULL)) OR ((provider = 'self_hosted'::text) AND (url IS NOT NULL))))`
- `CHECK (((NOT is_active) OR (status = 'ready'::text)))`
- `PRIMARY KEY (id)`
- `UNIQUE (layer_id, version)`
- `EXCLUDE USING btree (layer_id WITH =) WHERE (is_active) DEFERRABLE INITIALLY DEFERRED`
- `FOREIGN KEY (layer_id) REFERENCES catalog.layer(id)`
- `FOREIGN KEY (status) REFERENCES catalog.ref_tileset_status(code)`

Indexes:

- `CREATE UNIQUE INDEX tileset_layer_id_version_key USING btree (layer_id, version)`
- `CREATE INDEX tileset_one_active_per_layer USING btree (layer_id) WHERE is_active`

Triggers:

- `a_stamp`: BEFORE INSERT OR UPDATE ON catalog.tileset FOR EACH ROW runs `catalog.stamp()`
- `z_audit`: AFTER INSERT OR DELETE OR UPDATE ON catalog.tileset FOR EACH ROW runs `audit.log_change('id')`
- `b_guard`: BEFORE INSERT OR UPDATE ON catalog.tileset FOR EACH ROW runs `catalog.tileset_guard()`
- `y_published_needs_tileset`: AFTER INSERT OR DELETE OR UPDATE ON catalog.tileset DEFERRABLE INITIALLY DEFERRED FOR EACH ROW runs `catalog.check_published_has_tileset()`

## catalog.layer_feature_stats

*materialized view.* Number of 3D objects per dataset tag, class and LOD. Refreshed after each import.

| Column | Type | Null | Default | Meaning |
|---|---|---|---|---|
| `lineage` | `text` | yes | - | - |
| `dataset_code` | `text` | yes | - | - |
| `theme_code` | `text` | yes | - | - |
| `classname` | `text` | yes | - | - |
| `is_toplevel` | `boolean` | yes | - | - |
| `lod` | `text` | yes | - | - |
| `feature_count` | `bigint` | yes | - | - |

Indexes:

- `CREATE UNIQUE INDEX layer_feature_stats_uq USING btree (lineage, classname, lod)`

# Schema audit

Automatic history of every change to catalog data.

## audit.change_log

*table.* Automatic history of every change to catalog data: who, when, old and new values. Cannot be edited.

| Column | Type | Null | Default | Meaning |
|---|---|---|---|---|
| `id` | `bigint` | no | `nextval('audit.change_log_id_seq'::regclass)` | - |
| `at` | `timestamp with time zone` | no | `now()` | - |
| `app_user` | `text` | no | - | - |
| `db_user` | `text` | no | `CURRENT_USER` | - |
| `table_name` | `text` | no | - | - |
| `pk` | `text` | no | - | - |
| `op` | `text` | no | - | - |
| `old_row` | `jsonb` | yes | - | - |
| `new_row` | `jsonb` | yes | - | - |

Constraints:

- `CHECK ((op = ANY (ARRAY['INSERT'::text, 'UPDATE'::text, 'DELETE'::text])))`
- `PRIMARY KEY (id)`

Indexes:

- `CREATE INDEX change_log_record_ix USING btree (table_name, pk, at DESC)`
- `CREATE INDEX change_log_user_ix USING btree (app_user, at DESC)`
- `CREATE INDEX change_log_at_ix USING btree (at DESC)`

# Functions

| Function | Returns | Security | Purpose |
|---|---|---|---|
| `audit.current_app_user()` | `text` | invoker | Returns the signed-in user name for this transaction; raises an error when empty. |
| `audit.log_change()` | `trigger` | definer | Trigger: writes one row to audit.change_log with old and new values. |
| `catalog.check_published_has_tileset()` | `trigger` | invoker | Deferred constraint trigger: a published layer has exactly one active Ready tileset at COMMIT. |
| `catalog.dataset_archive_cascade()` | `trigger` | invoker | Trigger: archiving a dataset archives its layers. |
| `catalog.dataset_guard()` | `trigger` | definer | Trigger: blocks changing dataset.code once 3D objects carry the lineage tag. |
| `catalog.import_job_guard()` | `trigger` | invoker | Trigger: sets finished_at when a job leaves the running state. |
| `catalog.layer_archive_cascade()` | `trigger` | invoker | Trigger: archiving a layer retires its tilesets. |
| `catalog.layer_guard()` | `trigger` | invoker | Trigger: layer identity freeze, allowed status moves, validation and publish preconditions. |
| `catalog.layer_lineage(dataset_code text, theme_code text, lod smallint)` | `text` | invoker | Builds the lineage tag dataset.theme.lodN for a layer. |
| `catalog.refresh_derived()` | `void` | definer | Run after an import: refreshes object counts and recomputes bounding boxes. |
| `catalog.stamp()` | `trigger` | invoker | Trigger: fills created_by/updated_by and timestamps from the signed-in user. |
| `catalog.tileset_guard()` | `trigger` | invoker | Trigger: allowed tileset status moves and published_at. |

# Roles and privileges

Roles are NOLOGIN group roles. Create one login user per person or service and grant one group, for example `CREATE ROLE api LOGIN PASSWORD '...' IN ROLE twin_ro;`.

| Role | Login | Member of | Purpose |
|---|---|---|---|
| `twin_admin` | no | twin_editor | Editor rights plus DELETE and refresh_derived() |
| `twin_api` | yes | twin_ro | Login used by the read-only API (member of twin_ro, read-only transactions) |
| `twin_app` | yes | twin_admin | Login used by the Django admin (member of twin_admin; Django groups decide who edits or deletes) |
| `twin_editor` | no | twin_ro | Insert and update catalog rows (admin UI) |
| `twin_importer` | no | twin_editor | Editor rights plus writing citydb (citydb-tool) and refresh_derived() |
| `twin_ro` | no | - | Read catalog, audit and the 3D city model (API, viewers) |
