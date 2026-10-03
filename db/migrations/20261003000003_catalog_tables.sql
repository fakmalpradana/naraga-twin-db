-- migrate:up
CREATE TABLE catalog.dataset (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code            text NOT NULL UNIQUE CHECK (code ~ '^[a-z0-9_]+$'),
  name            text NOT NULL,
  description     text,
  region          text,
  source_system   text,
  owner_org       text,
  contact_email   text,
  generated_by    text NOT NULL DEFAULT 'external' REFERENCES catalog.ref_generated_by(code),
  crs_epsg        integer,
  vertical_datum  text NOT NULL DEFAULT 'unknown' CHECK (vertical_datum IN ('ellipsoid','egm2008','egm96','local','unknown')),
  license         text,
  attribution     text,
  bbox            geometry(Polygon, 4326),
  status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  created_by      text NOT NULL DEFAULT '',
  updated_at      timestamptz NOT NULL DEFAULT now(),
  updated_by      text NOT NULL DEFAULT ''
);
CREATE INDEX dataset_bbox_gix ON catalog.dataset USING gist (bbox);
COMMENT ON TABLE  catalog.dataset IS 'A collection of data for one area from one source, e.g. OIKN.';
COMMENT ON COLUMN catalog.dataset.code IS 'Short unique name (a-z, 0-9, _). It is the tag stored on every 3D object, so it cannot be changed once data is imported.';
COMMENT ON COLUMN catalog.dataset.crs_epsg IS 'Coordinate system of the SOURCE files (record only). The database itself has one fixed coordinate system.';
COMMENT ON COLUMN catalog.dataset.vertical_datum IS 'Height reference of the source: ellipsoid, egm2008, egm96, local or unknown. Needed to place tiles correctly in Cesium.';
COMMENT ON COLUMN catalog.dataset.attribution IS 'Credit line shown on the map (copyright/source).';
COMMENT ON COLUMN catalog.dataset.bbox IS 'Computed automatically from the data after import. Do not type by hand.';
COMMENT ON COLUMN catalog.dataset.status IS 'active or archived. Archiving a dataset archives all its layers.';
COMMENT ON COLUMN catalog.dataset.created_by IS 'Filled automatically with the signed-in user.';

CREATE TABLE catalog.import_job (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id      uuid NOT NULL REFERENCES catalog.dataset(id),
  tool            text NOT NULL DEFAULT 'citydb-tool',
  tool_version    text,
  citygml_version text,
  source_file     text NOT NULL,
  source_sha256   text CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
  source_size_bytes bigint,
  import_mode     text NOT NULL DEFAULT 'import_all' CHECK (import_mode IN ('import_all','skip','delete','terminate')),
  lineage_tag     text,
  status          text NOT NULL DEFAULT 'running' CHECK (status IN ('running','succeeded','failed')),
  validation_passed boolean,
  counts          jsonb NOT NULL DEFAULT '{}'::jsonb,
  report          jsonb NOT NULL DEFAULT '{}'::jsonb,
  started_at      timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  created_by      text NOT NULL DEFAULT '',
  updated_at      timestamptz NOT NULL DEFAULT now(),
  updated_by      text NOT NULL DEFAULT ''
);
CREATE INDEX import_job_dataset_ix ON catalog.import_job (dataset_id, started_at DESC);
COMMENT ON TABLE  catalog.import_job IS 'One uploaded/imported file and what happened to it (the upload registry).';
COMMENT ON COLUMN catalog.import_job.source_sha256 IS 'Fingerprint of the file, to prove which exact file was imported.';
COMMENT ON COLUMN catalog.import_job.validation_passed IS 'True when geometry/count checks (e.g. val3dity) passed. Required before a layer can be validated.';
COMMENT ON COLUMN catalog.import_job.lineage_tag IS 'Tag written on imported objects: dataset.theme.lodN.';
COMMENT ON COLUMN catalog.import_job.report IS 'Detailed validation report (JSON).';

CREATE TABLE catalog.layer (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id  uuid NOT NULL REFERENCES catalog.dataset(id),
  theme_code  text NOT NULL REFERENCES catalog.ref_theme(code),
  lod         smallint NOT NULL REFERENCES catalog.ref_lod_level(lod),
  title       text,
  description text,
  status      text NOT NULL DEFAULT 'draft' REFERENCES catalog.ref_layer_status(code),
  validated_by_job_id uuid REFERENCES catalog.import_job(id),
  bbox        geometry(Polygon, 4326),
  created_at  timestamptz NOT NULL DEFAULT now(),
  created_by  text NOT NULL DEFAULT '',
  updated_at  timestamptz NOT NULL DEFAULT now(),
  updated_by  text NOT NULL DEFAULT '',
  UNIQUE (dataset_id, theme_code, lod)
);
CREATE INDEX layer_lod_status_ix ON catalog.layer (lod, status);
CREATE INDEX layer_bbox_gix ON catalog.layer USING gist (bbox);
COMMENT ON TABLE  catalog.layer IS 'What the frontend chooses: one theme at one LOD of a dataset (e.g. OIKN buildings LOD1).';
COMMENT ON COLUMN catalog.layer.status IS 'draft -> validated -> published -> archived. Moves are checked by the database.';
COMMENT ON COLUMN catalog.layer.validated_by_job_id IS 'The successful import that justified validation.';
COMMENT ON COLUMN catalog.layer.bbox IS 'Computed automatically after import.';

CREATE TABLE catalog.import_job_layer (
  job_id   uuid NOT NULL REFERENCES catalog.import_job(id) ON DELETE CASCADE,
  layer_id uuid NOT NULL REFERENCES catalog.layer(id),
  PRIMARY KEY (job_id, layer_id)
);
COMMENT ON TABLE catalog.import_job_layer IS 'Which layers a file filled (one file can fill several).';

CREATE TABLE catalog.tileset (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  layer_id           uuid NOT NULL REFERENCES catalog.layer(id),
  version            integer NOT NULL CHECK (version > 0),
  provider           text NOT NULL CHECK (provider IN ('cesium_ion','self_hosted')),
  ion_asset_id       bigint,
  url                text,
  converter_name     text,
  converter_version  text,
  source_snapshot_at timestamptz,
  height_offset_m    numeric NOT NULL DEFAULT 0,
  status             text NOT NULL DEFAULT 'building' REFERENCES catalog.ref_tileset_status(code),
  is_active          boolean NOT NULL DEFAULT false,
  published_at       timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  created_by         text NOT NULL DEFAULT '',
  updated_at         timestamptz NOT NULL DEFAULT now(),
  updated_by         text NOT NULL DEFAULT '',
  UNIQUE (layer_id, version),
  CONSTRAINT tileset_provider_ck CHECK (
    (provider = 'cesium_ion'  AND ion_asset_id IS NOT NULL) OR
    (provider = 'self_hosted' AND url IS NOT NULL)),
  CONSTRAINT tileset_active_needs_ready_ck CHECK (NOT is_active OR status = 'ready'),
  -- at most one active tileset per layer; DEFERRABLE so a v1 -> v2 switch is one transaction
  CONSTRAINT tileset_one_active_per_layer EXCLUDE USING btree (layer_id WITH =) WHERE (is_active) DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE  catalog.tileset IS 'One 3D Tiles build of a layer. Only the active one is served.';
COMMENT ON COLUMN catalog.tileset.ion_asset_id IS 'Cesium ion asset number (required when provider is cesium_ion).';
COMMENT ON COLUMN catalog.tileset.url IS 'tileset.json address (required when provider is self_hosted).';
COMMENT ON COLUMN catalog.tileset.source_snapshot_at IS 'Data state used for this build. If the data changed later, the layer shows as stale.';
COMMENT ON COLUMN catalog.tileset.height_offset_m IS 'Vertical shift (metres) applied so buildings sit on the terrain.';
COMMENT ON COLUMN catalog.tileset.is_active IS 'The one build currently served. Only a Ready tileset can be active.';

-- migrate:down
DROP TABLE IF EXISTS catalog.tileset, catalog.import_job_layer, catalog.layer, catalog.import_job, catalog.dataset CASCADE;
