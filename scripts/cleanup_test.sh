#!/usr/bin/env bash
# Remove ALL dummy test data (dataset oikn_test): city objects by lineage, then catalog rows. Local docker compose.
# Usage: scripts/cleanup_test.sh [ROLLBACK]   (ROLLBACK = dry run of the catalog part)
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
END="${1:-COMMIT}"
NET=$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$(docker compose ps -q db)")
[ "$END" = COMMIT ] && docker run --rm --platform linux/amd64 --network "$NET" 3dcitydb/citydb-tool:1.4.0 delete \
  -H db -d postgres -u postgres -p "${POSTGRES_PASSWORD:-change-me}" --delete-mode=delete -f "lineage = 'oikn_test.building.lod1'"
docker compose exec -T db psql -U postgres -v ON_ERROR_STOP=1 -qAt <<SQL
BEGIN;
SELECT set_config('app.user', 'cleanup', false);
DELETE FROM catalog.import_job_layer WHERE layer_id IN (SELECT l.id FROM catalog.layer l JOIN catalog.dataset d ON d.id = l.dataset_id WHERE d.code = 'oikn_test');
DELETE FROM catalog.layer WHERE dataset_id = (SELECT id FROM catalog.dataset WHERE code = 'oikn_test');
DELETE FROM catalog.import_job WHERE dataset_id = (SELECT id FROM catalog.dataset WHERE code = 'oikn_test');
DELETE FROM catalog.dataset WHERE code = 'oikn_test';
SELECT 'remaining oikn_test features: ' || count(*) FROM citydb.feature WHERE lineage LIKE 'oikn_test.%' AND termination_date IS NULL;
$END;
SQL
rm -f viewer/oikn_test.geojson viewer/test_lod1.html data/osm_lod1.gml
