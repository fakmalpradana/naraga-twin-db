-- Dataset seed: IKN landmarks (LOD1 from LAS, generator-lod1). Idempotent. The layer starts empty and draft;
-- data comes in through POST /imports (see docs/PANDUAN_INPUT_LANDMARK.md).
SELECT set_config('app.user', 'seed', false);

INSERT INTO catalog.dataset (code, name, description, region, source_system, owner_org, generated_by, crs_epsg, vertical_datum, attribution)
VALUES ('landmark', 'IKN Landmark', 'LOD1 landmark buildings of IKN generated from LAS (generator-lod1, P95 height)', 'Ibu Kota Nusantara (IKN)',
        'generator-lod1', NULL, 'naraga', 4326, 'unknown' /* TODO vertical datum */, NULL)
ON CONFLICT (code) DO NOTHING;

INSERT INTO catalog.layer (dataset_id, theme_code, lod, title)
SELECT d.id, 'building', 1, 'IKN landmark buildings LOD1' FROM catalog.dataset d WHERE d.code = 'landmark'
ON CONFLICT (dataset_id, theme_code, lod) DO NOTHING;
