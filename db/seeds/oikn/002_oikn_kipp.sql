-- Dataset seed: OIKN KIPP landmark buildings (LOD1 from LAS, generator-lod1). Idempotent. The layer starts empty and draft;
-- data comes in through POST /imports (see docs/PANDUAN_INPUT_OIKN_KIPP.md).
SELECT set_config('app.user', 'seed', false);

INSERT INTO catalog.dataset (code, name, description, region, source_system, owner_org, generated_by, crs_epsg, vertical_datum, attribution)
VALUES ('oikn_kipp', 'OIKN KIPP', 'LOD1 landmark buildings of KIPP IKN generated from LAS (generator-lod1, P95 height)', 'Ibu Kota Nusantara (IKN)',
        'generator-lod1', NULL, 'naraga', 4326, 'unknown' /* TODO vertical datum */, NULL)
ON CONFLICT (code) DO NOTHING;

INSERT INTO catalog.layer (dataset_id, theme_code, lod, title)
SELECT d.id, 'building', 1, 'OIKN KIPP landmark buildings LOD1' FROM catalog.dataset d WHERE d.code = 'oikn_kipp'
ON CONFLICT (dataset_id, theme_code, lod) DO NOTHING;

INSERT INTO catalog.layer (dataset_id, theme_code, lod, title)
SELECT d.id, 'building', 3, 'OIKN KIPP landmark buildings LOD3' FROM catalog.dataset d WHERE d.code = 'oikn_kipp'
ON CONFLICT (dataset_id, theme_code, lod) DO NOTHING;
