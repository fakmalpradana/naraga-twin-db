-- migrate:up
-- Business rules live in the database so no UI/API/script can break them.

-- dataset.code is the lineage tag on 3D objects: freeze it once data exists.
CREATE FUNCTION catalog.dataset_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
  IF NEW.code IS DISTINCT FROM OLD.code AND EXISTS (
       SELECT 1 FROM citydb.feature
       WHERE lineage = OLD.code OR lineage LIKE replace(OLD.code, '_', '\_') || '.%') THEN
    RAISE EXCEPTION 'Dataset code "%" cannot be changed: 3D objects already carry it.', OLD.code;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER b_guard BEFORE UPDATE OF code ON catalog.dataset FOR EACH ROW EXECUTE FUNCTION catalog.dataset_guard();

-- Archiving a dataset archives its layers.
CREATE FUNCTION catalog.dataset_archive_cascade() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = 'archived' AND OLD.status <> 'archived' THEN
    UPDATE catalog.layer SET status = 'archived' WHERE dataset_id = NEW.id AND status <> 'archived';
  END IF;
  RETURN NULL;
END $$;
CREATE TRIGGER c_archive AFTER UPDATE OF status ON catalog.dataset FOR EACH ROW EXECUTE FUNCTION catalog.dataset_archive_cascade();

-- Layer: identity is frozen after the first import; status moves are checked.
CREATE FUNCTION catalog.layer_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE ok boolean; j catalog.import_job;
BEGIN
  IF (NEW.dataset_id, NEW.theme_code, NEW.lod) IS DISTINCT FROM (OLD.dataset_id, OLD.theme_code, OLD.lod)
     AND EXISTS (SELECT 1 FROM catalog.import_job_layer WHERE layer_id = OLD.id) THEN
    RAISE EXCEPTION 'Dataset, theme and LOD of a layer cannot be changed after data was imported into it.';
  END IF;

  IF NEW.status IS DISTINCT FROM OLD.status THEN
    ok := (OLD.status, NEW.status) IN (
      ('draft','validated'), ('draft','archived'),
      ('validated','draft'), ('validated','published'), ('validated','archived'),
      ('published','validated'), ('published','archived'),
      ('archived','draft'));
    IF NOT ok THEN
      RAISE EXCEPTION 'Layer status cannot move from % to %.', OLD.status, NEW.status;
    END IF;

    IF NEW.status = 'validated' AND OLD.status = 'draft' THEN
      SELECT * INTO j FROM catalog.import_job WHERE id = NEW.validated_by_job_id;
      IF NOT FOUND OR j.status <> 'succeeded' OR j.validation_passed IS NOT TRUE
         OR NOT EXISTS (SELECT 1 FROM catalog.import_job_layer WHERE job_id = j.id AND layer_id = NEW.id) THEN
        RAISE EXCEPTION 'A layer can only be validated by a successful import job with passed validation that filled this layer.';
      END IF;
    END IF;

    IF NEW.status = 'published' AND NOT EXISTS (
         SELECT 1 FROM catalog.tileset WHERE layer_id = NEW.id AND is_active AND status = 'ready') THEN
      RAISE EXCEPTION 'Cannot publish: the layer has no active Ready tileset.';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER b_guard BEFORE UPDATE ON catalog.layer FOR EACH ROW EXECUTE FUNCTION catalog.layer_guard();

-- Archiving a layer retires its tilesets so nothing is served.
CREATE FUNCTION catalog.layer_archive_cascade() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = 'archived' AND OLD.status <> 'archived' THEN
    UPDATE catalog.tileset SET is_active = false, status = 'retired'
     WHERE layer_id = NEW.id AND status IN ('building','ready');
  END IF;
  RETURN NULL;
END $$;
CREATE TRIGGER c_archive AFTER UPDATE OF status ON catalog.layer FOR EACH ROW EXECUTE FUNCTION catalog.layer_archive_cascade();

-- Tileset status moves + published_at.
CREATE FUNCTION catalog.tileset_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'UPDATE' AND NEW.status IS DISTINCT FROM OLD.status AND (OLD.status, NEW.status) NOT IN (
       ('building','ready'), ('building','failed'), ('building','retired'),
       ('ready','retired'), ('failed','building'), ('failed','retired')) THEN
    RAISE EXCEPTION 'Tileset status cannot move from % to %.', OLD.status, NEW.status;
  END IF;
  IF NEW.is_active AND NEW.published_at IS NULL THEN NEW.published_at := now(); END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER b_guard BEFORE INSERT OR UPDATE ON catalog.tileset FOR EACH ROW EXECUTE FUNCTION catalog.tileset_guard();

-- A published layer must have exactly one active Ready tileset, checked at COMMIT
-- so switching v1 -> v2 inside one transaction is fine.
CREATE FUNCTION catalog.check_published_has_tileset() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE lid uuid;
BEGIN
  IF TG_TABLE_NAME = 'layer' THEN lid := NEW.id;
  ELSIF TG_OP = 'DELETE' THEN lid := OLD.layer_id;
  ELSE lid := NEW.layer_id;
  END IF;
  IF EXISTS (SELECT 1 FROM catalog.layer WHERE id = lid AND status = 'published')
     AND (SELECT count(*) FROM catalog.tileset WHERE layer_id = lid AND is_active AND status = 'ready') <> 1 THEN
    RAISE EXCEPTION 'A published layer must have exactly one active Ready tileset.';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER y_published_needs_tileset AFTER INSERT OR UPDATE ON catalog.layer
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION catalog.check_published_has_tileset();
CREATE CONSTRAINT TRIGGER y_published_needs_tileset AFTER INSERT OR UPDATE OR DELETE ON catalog.tileset
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION catalog.check_published_has_tileset();

-- Import job: set finished_at automatically.
CREATE FUNCTION catalog.import_job_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status <> 'running' AND NEW.finished_at IS NULL THEN NEW.finished_at := now(); END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER b_guard BEFORE INSERT OR UPDATE ON catalog.import_job FOR EACH ROW EXECUTE FUNCTION catalog.import_job_guard();

-- migrate:down
DROP FUNCTION IF EXISTS catalog.import_job_guard(), catalog.check_published_has_tileset(), catalog.tileset_guard(),
  catalog.layer_archive_cascade(), catalog.layer_guard(), catalog.dataset_archive_cascade(), catalog.dataset_guard() CASCADE;
