-- Runnable check: psql -v ON_ERROR_STOP=1 -f db/tests/invariants.sql   (use a scratch database!)
-- Everything runs in one transaction that is rolled back at the end.
BEGIN;
SELECT set_config('app.user', 'tester', true);

CREATE TEMP TABLE r (name text, ok boolean);
CREATE OR REPLACE FUNCTION pg_temp.fails(sql text) RETURNS boolean LANGUAGE plpgsql AS $$
BEGIN EXECUTE sql; RETURN false; EXCEPTION WHEN OTHERS THEN RETURN true; END $$;

-- fixtures
INSERT INTO catalog.dataset (code, name) VALUES ('t_ds', 'Test');
INSERT INTO catalog.layer (dataset_id, theme_code, lod) SELECT id, 'building', 1 FROM catalog.dataset WHERE code='t_ds';
INSERT INTO catalog.import_job (dataset_id, source_file, status, validation_passed) SELECT id, 'x.gml', 'succeeded', true FROM catalog.dataset WHERE code='t_ds';

INSERT INTO r SELECT 'bad dataset code rejected', pg_temp.fails($$INSERT INTO catalog.dataset (code,name) VALUES ('Bad Code','x')$$);
INSERT INTO r SELECT 'lod 5 rejected', pg_temp.fails($$INSERT INTO catalog.layer (dataset_id, theme_code, lod) SELECT id,'building',5 FROM catalog.dataset WHERE code='t_ds'$$);
INSERT INTO r SELECT 'duplicate layer rejected', pg_temp.fails($$INSERT INTO catalog.layer (dataset_id, theme_code, lod) SELECT id,'building',1 FROM catalog.dataset WHERE code='t_ds'$$);
INSERT INTO r SELECT 'validate without job rejected', pg_temp.fails($$UPDATE catalog.layer SET status='validated' WHERE title IS NULL AND status='draft'$$);
INSERT INTO r SELECT 'draft->published rejected', pg_temp.fails($$UPDATE catalog.layer SET status='published' WHERE status='draft'$$);

INSERT INTO catalog.import_job_layer SELECT j.id, l.id FROM catalog.import_job j, catalog.layer l JOIN catalog.dataset d ON d.id=l.dataset_id WHERE d.code='t_ds';
UPDATE catalog.layer SET validated_by_job_id=(SELECT id FROM catalog.import_job WHERE source_file='x.gml'), status='validated' WHERE status='draft' AND dataset_id=(SELECT id FROM catalog.dataset WHERE code='t_ds');
INSERT INTO r SELECT 'validated with passed job ok', (SELECT status='validated' FROM catalog.layer WHERE dataset_id=(SELECT id FROM catalog.dataset WHERE code='t_ds'));
INSERT INTO r SELECT 'publish without tileset rejected', pg_temp.fails($$UPDATE catalog.layer SET status='published' WHERE status='validated'$$);
INSERT INTO r SELECT 'ion tileset without asset id rejected', pg_temp.fails($$INSERT INTO catalog.tileset (layer_id,version,provider) SELECT id,1,'cesium_ion' FROM catalog.layer WHERE status='validated'$$);

INSERT INTO catalog.tileset (layer_id,version,provider,ion_asset_id,status) SELECT id,1,'cesium_ion',111,'building' FROM catalog.layer WHERE status='validated';
INSERT INTO r SELECT 'active needs ready', pg_temp.fails($$UPDATE catalog.tileset SET is_active=true WHERE version=1$$);
UPDATE catalog.tileset SET status='ready' WHERE version=1;
UPDATE catalog.tileset SET is_active=true WHERE version=1;
UPDATE catalog.layer SET status='published' WHERE status='validated';
INSERT INTO r SELECT 'published ok', (SELECT status='published' FROM catalog.layer WHERE dataset_id=(SELECT id FROM catalog.dataset WHERE code='t_ds'));

-- switch v1 -> v2 atomically, and a second active tileset must be rejected
INSERT INTO catalog.tileset (layer_id,version,provider,ion_asset_id,status) SELECT layer_id,2,'cesium_ion',222,'ready' FROM catalog.tileset WHERE version=1;
SET CONSTRAINTS ALL IMMEDIATE;
INSERT INTO r SELECT 'second active tileset rejected', pg_temp.fails($$UPDATE catalog.tileset SET is_active=true WHERE version=2$$);
SET CONSTRAINTS ALL DEFERRED;
UPDATE catalog.tileset SET is_active=false, status='retired' WHERE version=1;
UPDATE catalog.tileset SET is_active=true WHERE version=2;
SET CONSTRAINTS ALL IMMEDIATE;
INSERT INTO r SELECT 'v1->v2 switch ok', (SELECT count(*)=1 FROM catalog.tileset WHERE is_active AND version=2);
INSERT INTO r SELECT 'retire active tileset of published layer rejected', pg_temp.fails($$UPDATE catalog.tileset SET is_active=false, status='retired' WHERE version=2$$);
SET CONSTRAINTS ALL DEFERRED;

-- archive cascades to tilesets
UPDATE catalog.layer SET status='archived' WHERE status='published';
INSERT INTO r SELECT 'archive retires tilesets', (SELECT count(*)=0 FROM catalog.tileset WHERE is_active);

-- dataset.code frozen when features carry the tag
INSERT INTO citydb.feature (objectclass_id, objectid, lineage) VALUES (901, 'b1', 't_ds.building.lod1');
INSERT INTO r SELECT 'code rename blocked when features exist', pg_temp.fails($$UPDATE catalog.dataset SET code='t_new' WHERE code='t_ds'$$);

-- audit: written with the real user; missing user rejected
INSERT INTO r SELECT 'audit row has app user', EXISTS (SELECT 1 FROM audit.change_log WHERE app_user='tester' AND table_name='catalog.layer' AND op='UPDATE');
INSERT INTO r SELECT 'created_by stamped', (SELECT bool_and(created_by='tester') FROM catalog.layer WHERE dataset_id=(SELECT id FROM catalog.dataset WHERE code='t_ds'));
SELECT set_config('app.user', '', true);
INSERT INTO r SELECT 'write without user rejected', pg_temp.fails($$UPDATE catalog.dataset SET description='x'$$);
SELECT set_config('app.user', 'tester', true);

-- derived data
SELECT catalog.refresh_derived();
INSERT INTO r SELECT 'stats count top-level features', (SELECT feature_count=1 FROM catalog.v_layer WHERE dataset_code='t_ds');

SELECT name, CASE WHEN ok THEN 'PASS' ELSE 'FAIL' END AS result FROM r;
DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_temp.r WHERE NOT ok) THEN RAISE EXCEPTION 'invariant tests FAILED'; END IF; END $$;
ROLLBACK;
