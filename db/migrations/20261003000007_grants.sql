-- migrate:up
GRANT SELECT ON ALL TABLES IN SCHEMA catalog TO twin_ro;
GRANT SELECT ON audit.change_log TO twin_ro;
GRANT INSERT, UPDATE ON catalog.dataset, catalog.layer, catalog.tileset, catalog.import_job, catalog.import_job_layer TO twin_editor;
GRANT INSERT, UPDATE ON catalog.ref_theme, catalog.ref_generated_by TO twin_editor;
GRANT DELETE ON ALL TABLES IN SCHEMA catalog TO twin_admin;
GRANT EXECUTE ON FUNCTION catalog.refresh_derived() TO twin_importer, twin_admin;

-- migrate:down
REVOKE ALL ON ALL TABLES IN SCHEMA catalog FROM twin_ro, twin_editor, twin_admin;
REVOKE ALL ON audit.change_log FROM twin_ro;
REVOKE EXECUTE ON FUNCTION catalog.refresh_derived() FROM twin_importer, twin_admin;
