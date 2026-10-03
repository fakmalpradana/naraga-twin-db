-- migrate:up
-- Every write to catalog tables must name a user: SELECT set_config('app.user', '<name>', true);
-- Admin/API do this per transaction; CLI imports pass --user. No user => the write is rejected.

CREATE TABLE audit.change_log (
  id         bigserial PRIMARY KEY,
  at         timestamptz NOT NULL DEFAULT now(),
  app_user   text NOT NULL,
  db_user    text NOT NULL DEFAULT current_user,
  table_name text NOT NULL,
  pk         text NOT NULL,
  op         text NOT NULL CHECK (op IN ('INSERT','UPDATE','DELETE')),
  old_row    jsonb,
  new_row    jsonb
);
CREATE INDEX change_log_record_ix ON audit.change_log (table_name, pk, at DESC);
CREATE INDEX change_log_user_ix   ON audit.change_log (app_user, at DESC);
CREATE INDEX change_log_at_ix     ON audit.change_log (at DESC);
COMMENT ON TABLE audit.change_log IS 'Automatic history of every change to catalog data: who, when, old and new values. Cannot be edited.';

CREATE FUNCTION audit.current_app_user() RETURNS text
LANGUAGE plpgsql STABLE AS $$
DECLARE u text := nullif(btrim(current_setting('app.user', true)), '');
BEGIN
  IF u IS NULL THEN
    RAISE EXCEPTION 'A user name is required to change data (set app.user). Sign in, or pass the user name to the import command.'
      USING ERRCODE = 'P0001';
  END IF;
  RETURN u;
END $$;

-- Fills created_*/updated_* from the signed-in user; client-supplied values are overwritten.
CREATE FUNCTION catalog.stamp() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE u text := audit.current_app_user();
BEGIN
  IF TG_OP = 'INSERT' THEN
    NEW.created_at := now(); NEW.created_by := u;
  ELSE
    NEW.created_at := OLD.created_at; NEW.created_by := OLD.created_by;
  END IF;
  NEW.updated_at := now(); NEW.updated_by := u;
  RETURN NEW;
END $$;

-- Row history. TG_ARGV = primary key column names.
CREATE FUNCTION audit.log_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, audit AS $$
DECLARE
  u text := audit.current_app_user();
  j jsonb := to_jsonb(CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END);
  k text;
  parts text[] := '{}';
BEGIN
  IF TG_OP = 'UPDATE' AND (to_jsonb(NEW) - 'updated_at' - 'updated_by') = (to_jsonb(OLD) - 'updated_at' - 'updated_by') THEN
    RETURN NULL;   -- nothing really changed
  END IF;
  FOREACH k IN ARRAY TG_ARGV LOOP parts := parts || (j ->> k); END LOOP;
  INSERT INTO audit.change_log (app_user, table_name, pk, op, old_row, new_row)
  VALUES (u, TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME, array_to_string(parts, ':'), TG_OP,
          CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END,
          CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END);
  RETURN NULL;
END $$;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['dataset','import_job','layer','tileset'] LOOP
    EXECUTE format('CREATE TRIGGER a_stamp BEFORE INSERT OR UPDATE ON catalog.%I FOR EACH ROW EXECUTE FUNCTION catalog.stamp()', t);
    EXECUTE format('CREATE TRIGGER z_audit AFTER INSERT OR UPDATE OR DELETE ON catalog.%I FOR EACH ROW EXECUTE FUNCTION audit.log_change(%L)', t, 'id');
  END LOOP;
  FOREACH t IN ARRAY ARRAY['ref_lod_level:lod','ref_theme:code','ref_generated_by:code','ref_layer_status:code','ref_tileset_status:code'] LOOP
    EXECUTE format('CREATE TRIGGER z_audit AFTER INSERT OR UPDATE OR DELETE ON catalog.%I FOR EACH ROW EXECUTE FUNCTION audit.log_change(%L)', split_part(t,':',1), split_part(t,':',2));
  END LOOP;
END $$;
CREATE TRIGGER z_audit AFTER INSERT OR UPDATE OR DELETE ON catalog.import_job_layer
  FOR EACH ROW EXECUTE FUNCTION audit.log_change('job_id', 'layer_id');

-- migrate:down
DROP TABLE IF EXISTS audit.change_log CASCADE;
DROP FUNCTION IF EXISTS audit.log_change() CASCADE;
DROP FUNCTION IF EXISTS catalog.stamp() CASCADE;
DROP FUNCTION IF EXISTS audit.current_app_user() CASCADE;
