#!/usr/bin/env bash
# Register a built tileset folder as the layer's NEW active tileset (old one is retired in the same transaction).
# Usage: scripts/publish_tiles.sh USER=name DATASET=oikn THEME=building LOD=1 VERSION=2 [HEIGHT_OFFSET=0] [DRY=1]
# Needs tiles/<DATASET>/<THEME>/lod<LOD>/v<VERSION>/tileset.json (see scripts/build_tiles.py) and, for a first publish, a layer in
# 'validated' status. Local docker compose; for a remote DB run the SQL below through psql instead.
set -euo pipefail
for kv in "$@"; do export "$kv"; done
: "${USER:?}" "${DATASET:?}" "${THEME:?}" "${LOD:?}" "${VERSION:?}"
cd "$(dirname "$0")/.."
DIR="tiles/$DATASET/$THEME/lod$LOD/v$VERSION"
[ -f "$DIR/tileset.json" ] || { echo "missing $DIR/tileset.json (run scripts/build_tiles.py first)" >&2; exit 1; }
END=COMMIT; CHK=""; [ -n "${DRY:-}" ] && { END=ROLLBACK; CHK="SET CONSTRAINTS ALL IMMEDIATE;"; }   # dry run still checks the deferred rules
docker compose exec -T db psql -U postgres -v ON_ERROR_STOP=1 -qAt -v u="$USER" <<SQL
SELECT set_config('app.user', :'u', false) \gset
BEGIN;
CREATE TEMP TABLE _l AS SELECT l.id FROM catalog.layer l JOIN catalog.dataset d ON d.id = l.dataset_id
  WHERE d.code = '$DATASET' AND l.theme_code = '$THEME' AND l.lod = $LOD;
INSERT INTO catalog.tileset (layer_id, version, provider, url, converter_name, converter_version, source_snapshot_at, height_offset_m, status, is_active)
  SELECT id, $VERSION, 'self_hosted', '/$DIR/tileset.json', 'scripts/build_tiles.py', '1', now(), ${HEIGHT_OFFSET:-0}, 'ready', false FROM _l;
UPDATE catalog.tileset SET is_active = false, status = 'retired' WHERE layer_id = (SELECT id FROM _l) AND is_active;
UPDATE catalog.tileset SET is_active = true WHERE layer_id = (SELECT id FROM _l) AND version = $VERSION;
UPDATE catalog.layer SET status = 'published' WHERE id = (SELECT id FROM _l) AND status = 'validated';
$CHK
$END;
SQL
[ "$END" = COMMIT ] && echo "$DATASET.$THEME.lod$LOD now serves v$VERSION" || echo "dry run OK (rolled back)"
