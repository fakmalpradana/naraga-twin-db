-- migrate:up
-- Guard: 3DCityDB v5 must already exist (the 3dcitydb-pg image creates it on first start).
DO $$
BEGIN
  IF to_regclass('citydb.feature') IS NULL THEN
    RAISE EXCEPTION 'Schema citydb (3DCityDB v5) not found. Start the 3dcitydb-pg v5 image first.';
  END IF;
END $$;

CREATE SCHEMA IF NOT EXISTS catalog;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS app;      -- Django's own tables live here, never mixed with catalog

COMMENT ON SCHEMA catalog IS 'Catalog of datasets, layers, tilesets and import history. Safe to edit by trained staff via the admin UI.';
COMMENT ON SCHEMA audit   IS 'Who changed what and when (automatic, read-only).';
COMMENT ON SCHEMA app     IS 'Admin/API application tables (users, sessions). Not part of the data model.';

-- Group roles (NOLOGIN). Create one login user per person/service and GRANT one of these.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'twin_ro')       THEN CREATE ROLE twin_ro NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'twin_editor')   THEN CREATE ROLE twin_editor NOLOGIN IN ROLE twin_ro; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'twin_admin')    THEN CREATE ROLE twin_admin NOLOGIN IN ROLE twin_editor; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'twin_importer') THEN CREATE ROLE twin_importer NOLOGIN IN ROLE twin_editor; END IF;
END $$;

GRANT USAGE ON SCHEMA catalog, audit, citydb TO twin_ro;
GRANT USAGE ON SCHEMA app TO twin_editor;

-- API/read role may read the 3D city model; importer may write it (citydb-tool).
GRANT SELECT ON ALL TABLES IN SCHEMA citydb TO twin_ro;
GRANT INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA citydb TO twin_importer;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA citydb TO twin_importer;

-- migrate:down
REVOKE ALL ON ALL TABLES IN SCHEMA citydb FROM twin_importer;
REVOKE USAGE ON ALL SEQUENCES IN SCHEMA citydb FROM twin_importer;
REVOKE SELECT ON ALL TABLES IN SCHEMA citydb FROM twin_ro;
REVOKE USAGE ON SCHEMA citydb FROM twin_ro;
DROP SCHEMA IF EXISTS app CASCADE;
DROP SCHEMA IF EXISTS audit CASCADE;
DROP SCHEMA IF EXISTS catalog CASCADE;
-- Roles are cluster-wide and intentionally kept.
