-- migrate:up
-- The only change to the 3DCityDB schema: an index so lineage tags can be searched quickly.
-- Re-run `make migrate` after any 3DCityDB upgrade (IF NOT EXISTS keeps it idempotent).
CREATE INDEX IF NOT EXISTS feature_lineage_inx ON citydb.feature (lineage text_pattern_ops);

-- Counts per tag / class / LOD. Refresh after each import: SELECT catalog.refresh_stats();
-- ponytail: scans property for val_lod (indexed); if it gets slow with millions of rows, summarise per import job instead.
CREATE MATERIALIZED VIEW catalog.layer_feature_stats AS
SELECT f.lineage,
       split_part(f.lineage, '.', 1) AS dataset_code,
       split_part(f.lineage, '.', 2) AS theme_code,
       oc.classname,
       (oc.is_toplevel = 1)          AS is_toplevel,
       COALESCE(p.val_lod, '')       AS lod,
       count(DISTINCT f.id)          AS feature_count
FROM citydb.feature f
JOIN citydb.objectclass oc ON oc.id = f.objectclass_id
LEFT JOIN citydb.property p ON p.feature_id = f.id AND p.val_lod IS NOT NULL
WHERE f.lineage IS NOT NULL AND f.termination_date IS NULL
GROUP BY f.lineage, oc.classname, oc.is_toplevel, COALESCE(p.val_lod, '');
CREATE UNIQUE INDEX layer_feature_stats_uq ON catalog.layer_feature_stats (lineage, classname, lod);
COMMENT ON MATERIALIZED VIEW catalog.layer_feature_stats IS 'Number of 3D objects per dataset tag, class and LOD. Refreshed after each import.';

CREATE FUNCTION catalog.layer_lineage(dataset_code text, theme_code text, lod smallint) RETURNS text
LANGUAGE sql IMMUTABLE AS $$ SELECT dataset_code || '.' || theme_code || '.lod' || lod $$;

-- Recompute bounding boxes from the data (never typed by hand) and refresh counts.
CREATE FUNCTION catalog.refresh_derived() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE s integer;
BEGIN
  PERFORM audit.current_app_user();
  SELECT srid INTO s FROM citydb.database_srs LIMIT 1;
  REFRESH MATERIALIZED VIEW CONCURRENTLY catalog.layer_feature_stats;

  UPDATE catalog.layer l SET bbox = b.box
  FROM (SELECT l2.id,
               (SELECT ST_Transform(ST_SetSRID(ST_Extent(f.envelope)::geometry, s), 4326)
                  FROM citydb.feature f
                 WHERE f.lineage = catalog.layer_lineage(d.code, l2.theme_code, l2.lod)
                   AND f.termination_date IS NULL) AS box
          FROM catalog.layer l2 JOIN catalog.dataset d ON d.id = l2.dataset_id) b
  WHERE l.id = b.id AND b.box IS NOT NULL AND l.bbox IS DISTINCT FROM ST_Envelope(b.box);

  UPDATE catalog.dataset d SET bbox = b.box
  FROM (SELECT dataset_id, ST_Envelope(ST_Extent(bbox)::geometry) AS box FROM catalog.layer
         WHERE bbox IS NOT NULL GROUP BY dataset_id) b
  WHERE d.id = b.dataset_id AND d.bbox IS DISTINCT FROM ST_SetSRID(b.box, 4326);
END $$;
COMMENT ON FUNCTION catalog.refresh_derived() IS 'Run after an import: refreshes object counts and bounding boxes. Needs a user name (app.user).';

-- Read model for the API and the admin inventory.
CREATE VIEW catalog.v_layer AS
SELECT l.id AS layer_id, d.id AS dataset_id, d.code AS dataset_code, d.name AS dataset_name,
       d.generated_by, d.attribution, d.vertical_datum, d.region,
       l.theme_code, l.lod, l.title, l.status,
       ST_XMin(l.bbox) AS bbox_minx, ST_YMin(l.bbox) AS bbox_miny,
       ST_XMax(l.bbox) AS bbox_maxx, ST_YMax(l.bbox) AS bbox_maxy,
       l.bbox,
       COALESCE((SELECT sum(s.feature_count) FROM catalog.layer_feature_stats s
                  WHERE s.lineage = catalog.layer_lineage(d.code, l.theme_code, l.lod) AND s.is_toplevel), 0)::bigint AS feature_count,
       t.id AS tileset_id, t.version AS tileset_version, t.provider, t.ion_asset_id, t.url,
       t.height_offset_m, t.published_at, t.source_snapshot_at,
       (t.id IS NOT NULL AND t.source_snapshot_at IS NOT NULL AND EXISTS (
          SELECT 1 FROM citydb.feature f
           WHERE f.lineage = catalog.layer_lineage(d.code, l.theme_code, l.lod)
             AND f.last_modification_date > t.source_snapshot_at)) AS is_stale,
       l.updated_at, l.updated_by
FROM catalog.layer l
JOIN catalog.dataset d ON d.id = l.dataset_id
LEFT JOIN catalog.tileset t ON t.layer_id = l.id AND t.is_active;
COMMENT ON VIEW catalog.v_layer IS 'One row per layer with its active tileset, object count and stale flag. Used by the API and the inventory.';

-- migrate:down
DROP VIEW IF EXISTS catalog.v_layer;
DROP FUNCTION IF EXISTS catalog.refresh_derived(), catalog.layer_lineage(text, text, smallint) CASCADE;
DROP MATERIALIZED VIEW IF EXISTS catalog.layer_feature_stats;
DROP INDEX IF EXISTS citydb.feature_lineage_inx;
