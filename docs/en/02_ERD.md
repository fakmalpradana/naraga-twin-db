# Introduction

This document shows the entities of the Digital Twin Catalog and how they relate to the 3DCityDB city model. Diagrams are split by topic so each one stays readable. The table and column list of every entity is in the *Database Schema* document, which is generated from the live database.

Verified against 3DCityDB v5.1.4 (image `3dcitydb/3dcitydb-pg:16-3.4-5.1.4`) on 3 October 2026.

## How to read the diagrams

| Notation | Meaning |
|---|---|
| `\|\|--o{` | One parent row has zero or many child rows; the child must have a parent |
| `\|o--o{` | Optional parent: the foreign key may be empty |
| PK, FK, UK | Primary key, foreign key, unique key |
| Dotted line | Logical link through a tag, not a database foreign key |
| `citydb_` prefix | Table of the 3DCityDB schema `citydb` (owned by 3DCityDB, shown for context) |

To keep the diagrams readable, two groups of columns are left out:

- **Audit columns** on `dataset`, `import_job`, `layer` and `tileset`: `created_at`, `created_by`, `updated_at`, `updated_by`.
- **Lookup references** to code lists. They are listed in the section *Code lists*.

# Overview

```{.mermaid}
flowchart TB
  A["Catalog<br/>dataset, layer, tileset,<br/>import_job"]
  B["City Model<br/>3DCityDB v5<br/>schema citydb"]
  C["Tile Publishing<br/>convertwin + Cesium ion"]
  D["Administration and Audit<br/>users, change_log"]
  A -. "lineage tag" .-> B
  B --> C
  C -- "ion_asset_id, url" --> A
  D -- "every write" --> A
```
<p class="figure-note">Figure 1. Contexts. The dotted line is the lineage tag; the other lines are data flows.</p>

| Context | Entities |
|---|---|
| Catalog | `dataset`, `layer`, `tileset`, `import_job`, `import_job_layer`, code lists |
| City Model | `citydb.feature`, `citydb.property`, `citydb.objectclass` and 14 more 3DCityDB tables |
| Tile Publishing | No tables of its own; writes `tileset` rows |
| Administration and Audit | `audit.change_log`, Django tables in schema `app` |

# Catalog core

A dataset holds many layers (one per theme and LOD). A layer has many tileset builds, and at most one is active.

```{.mermaid}
erDiagram
  dataset ||--o{ layer : dataset_id
  layer ||--o{ tileset : layer_id
  dataset {
    uuid id PK
    text code UK
    text name
    text vertical_datum
    text status
  }
  layer {
    uuid id PK
    uuid dataset_id FK
    text theme_code
    smallint lod
    text status
  }
  tileset {
    uuid id PK
    uuid layer_id FK
    int version
    text provider
    bigint ion_asset_id
    bool is_active
  }
```
<p class="figure-note">Figure 2. Catalog core, key columns only (all columns are in the Database Schema document). A layer is unique per dataset, theme and LOD; a tileset is unique per layer and version.</p>

| Parent | Child | Foreign key | Cardinality |
|---|---|---|---|
| `dataset` | `layer` | `dataset_id` | 1 to many (required) |
| `layer` | `tileset` | `layer_id` | 1 to many (required) |

Rules shown by this model: unique (`dataset_id`, `theme_code`, `lod`) on layer; unique (`layer_id`, `version`) on tileset; at most one `is_active` tileset per layer.

# Import traceability

Every file placed into the system is an `import_job`. One file can fill several layers, so jobs and layers are linked through `import_job_layer`.

```{.mermaid}
erDiagram
  dataset ||--o{ import_job : dataset_id
  import_job ||--o{ import_job_layer : job_id
  layer ||--o{ import_job_layer : layer_id
  import_job |o--o{ layer : validated_by_job_id
  import_job {
    uuid id PK
    uuid dataset_id FK
    text source_file
    text status
    bool validation_passed
  }
  import_job_layer {
    uuid job_id PK
    uuid layer_id PK
  }
  layer {
    uuid id PK
    text status
    uuid validated_by_job_id FK
  }
  dataset {
    uuid id PK
  }
```
<p class="figure-note">Figure 3. Import jobs and the layers they fill. Entities with only an id are shown for context.</p>

| Parent | Child | Foreign key | Cardinality |
|---|---|---|---|
| `dataset` | `import_job` | `dataset_id` | 1 to many (required) |
| `import_job` | `import_job_layer` | `job_id` | 1 to many (required) |
| `layer` | `import_job_layer` | `layer_id` | 1 to many (required) |
| `import_job` | `layer` | `validated_by_job_id` | 0..1 to many (optional) |

A layer can move from draft to validated only when `validated_by_job_id` points to a succeeded job with passed validation that is linked to the layer through `import_job_layer`.

# Link to the city model

The catalog never changes city objects. A layer is tied to its 3D objects by the **lineage tag** `dataset.theme.lodN`, written into `citydb.feature.lineage` at import time.

```{.mermaid}
erDiagram
  layer ||..o{ citydb_feature : lineage_tag
  citydb_feature ||--o{ citydb_property : feature_id
  citydb_objectclass ||--o{ citydb_feature : objectclass_id
  layer {
    uuid id PK
    text theme_code
    smallint lod
  }
  citydb_feature {
    bigint id PK
    int objectclass_id FK
    text objectid
    text lineage
    timestamptz last_modification_date
  }
  citydb_property {
    bigint id PK
    bigint feature_id FK
    text name
    text val_lod
  }
  citydb_objectclass {
    int id PK
    text classname
  }
```
<p class="figure-note">Figure 4. The dotted line is the lineage tag, not a foreign key. LOD is stored in <code>property.val_lod</code>.</p>

## Why a tag and not a link table

| Option | Decision | Reason |
|---|---|---|
| Link table `layer_feature` | Rejected | Millions of rows that duplicate what 3DCityDB already stores |
| One schema per dataset | Rejected | Multiplies API, admin and statistics work |
| Tag in `feature.lineage` | **Chosen** | Native column, indexed by `feature_lineage_inx`, no extra rows |

## 3DCityDB v5 facts the model relies on

| Fact | Detail |
|---|---|
| Tables | 17: `feature`, `property`, `geometry_data`, `implicit_geometry`, `appearance`, `surface_data`, `surface_data_mapping`, `appear_to_surface_data`, `tex_image`, `address`, `objectclass`, `datatype`, `namespace`, `codelist`, `codelist_entry`, `ade`, `database_srs` |
| LOD | Stored in `property.val_lod` (text) on geometry properties; `geometry_data` has no LOD column |
| LOD4 of CityGML 2.0 | Kept as deprecated-namespace properties; rooms are class `BuildingRoom` (id 904) |
| Lineage | `feature.lineage` exists and is not indexed by default; the catalog adds `feature_lineage_inx` |
| Class ids | Building 901, BuildingPart 902, BuildingRoom 904, Road 607, Bridge 1001, TINRelief 502 |
| SRID | Fixed per database at creation (`citydb.database_srs`) |

# Audit

Every insert, update and delete on catalog tables is logged with the signed-in user, the old row and the new row. The log is filled by triggers and cannot be edited.

| Column | Meaning |
|---|---|
| `at` | When the change happened |
| `app_user` | Signed-in user who made it (mandatory) |
| `table_name`, `pk` | Which record changed |
| `op` | INSERT, UPDATE or DELETE |
| `old_row`, `new_row` | Full row before and after, as JSON |

The audit log has no foreign keys. A record's history is found by `table_name` and `pk`.

# Code lists

Code lists are small reference tables used as foreign keys instead of PostgreSQL ENUM types, so the admin UI can show drop-down lists and administrators can add values without a migration.

| Code list | Values | Used by |
|---|---|---|
| `catalog.ref_lod_level` | 0 to 4 | `layer.lod` |
| `catalog.ref_theme` | building, bridge, road, terrain, vegetation, water, furniture, generic | `layer.theme_code` |
| `catalog.ref_generated_by` | external, naraga, manual | `dataset.generated_by` |
| `catalog.ref_layer_status` | draft, validated, published, archived | `layer.status` |
| `catalog.ref_tileset_status` | building, ready, failed, retired | `tileset.status` |

# Read model

| View | Built from | Description |
|---|---|---|
| `catalog.v_layer` | layer, dataset, tileset, layer_feature_stats, citydb.feature | One row per layer with its active tileset, object count and stale flag. Used by the API and the inventory page. |
| `catalog.layer_feature_stats` | citydb.feature, objectclass, property | Materialized view: object counts per tag, class and LOD. Refreshed after each import. |

Key indexes: `layer(lod, status)`, GiST on `layer.bbox` and `dataset.bbox`, `import_job(dataset_id, started_at desc)`, `change_log(table_name, pk, at desc)`, `change_log(app_user, at desc)`, `citydb.feature(lineage text_pattern_ops)`.
