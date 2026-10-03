#!/usr/bin/env bash
# Import one CityGML 2.0 file into 3DCityDB with traceability (job row, lineage tag, user, counts).
# Usage: scripts/import.sh USER=name FILE=path/to/file.gml DATASET=oikn THEME=building LOD=1 [MODE=import_all]
# Local docker compose by default. For a remote database (e.g. Railway TCP proxy) also pass
#   REMOTE_HOST=<host> REMOTE_PORT=<port> REMOTE_PASSWORD=<postgres password>
set -euo pipefail
for kv in "$@"; do export "$kv"; done
: "${USER:?USER=<your name> is required}" "${FILE:?FILE=... required}" "${DATASET:?}" "${THEME:?}" "${LOD:?}"
MODE="${MODE:-import_all}"
[ -f "$FILE" ] || { echo "file not found: $FILE" >&2; exit 1; }
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
TAG="${DATASET}.${THEME}.lod${LOD}"
SHA=$(shasum -a 256 "$FILE" | cut -d' ' -f1); SIZE=$(wc -c < "$FILE" | tr -d ' ')
if [ -n "${REMOTE_HOST:-}" ]; then
  : "${REMOTE_PORT:?}" "${REMOTE_PASSWORD:?}"
  psql_db() { PGPASSWORD="$REMOTE_PASSWORD" psql -h "$REMOTE_HOST" -p "$REMOTE_PORT" -U postgres -d postgres -v ON_ERROR_STOP=1 -qAt -v u="$USER" "$@"; }
  DB_ARGS=(-H "$REMOTE_HOST" -P "$REMOTE_PORT" -d postgres -u postgres -p "$REMOTE_PASSWORD"); NET_ARGS=()
else
  psql_db() { docker compose exec -T db psql -U postgres -v ON_ERROR_STOP=1 -qAt -v u="$USER" "$@"; }
  DB_ARGS=(-H db -d postgres -u postgres -p "${POSTGRES_PASSWORD:-change-me}")
  NET_ARGS=(--network "$(docker compose ps -q db | xargs docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')")
fi
SQL_USER="SELECT set_config('app.user', :'u', false) \\gset"

JOB=$(psql_db <<SQL | tail -1
$SQL_USER
INSERT INTO catalog.layer (dataset_id, theme_code, lod, title)
  SELECT id, '$THEME', $LOD, '$DATASET $THEME LOD$LOD' FROM catalog.dataset WHERE code = '$DATASET'
  ON CONFLICT (dataset_id, theme_code, lod) DO NOTHING;
WITH j AS (
  INSERT INTO catalog.import_job (dataset_id, source_file, source_sha256, source_size_bytes, import_mode, lineage_tag, tool_version, citygml_version)
  SELECT id, '$(basename "$FILE")', '$SHA', $SIZE, '$MODE', '$TAG', '1.4.0', '2.0' FROM catalog.dataset WHERE code = '$DATASET' RETURNING id, dataset_id),
l AS (INSERT INTO catalog.import_job_layer SELECT j.id, l.id FROM j JOIN catalog.layer l ON l.dataset_id = j.dataset_id AND l.theme_code = '$THEME' AND l.lod = $LOD RETURNING job_id)
SELECT id FROM j;
SQL
)
[ -n "$JOB" ] || { echo "dataset '$DATASET' not found" >&2; exit 1; }
echo "import job $JOB, lineage $TAG"

set +e
docker run --rm --platform linux/amd64 "${NET_ARGS[@]}" \
  -v "$(cd "$(dirname "$FILE")" && pwd)":/data:ro 3dcitydb/citydb-tool:1.4.0 import citygml \
  "${DB_ARGS[@]}" \
  --lineage "$TAG" --reason-for-update "import_job:$JOB" --updating-person "$USER" -m "$MODE" --compute-extent \
  "/data/$(basename "$FILE")"
RC=$?
set -e
STATUS=succeeded; [ $RC -eq 0 ] || STATUS=failed
psql_db <<SQL
$SQL_USER
UPDATE catalog.import_job SET status = '$STATUS',
  counts = (SELECT COALESCE(jsonb_object_agg(classname, n), '{}') FROM
            (SELECT oc.classname, count(*) n FROM citydb.feature f JOIN citydb.objectclass oc ON oc.id = f.objectclass_id
              WHERE f.lineage = '$TAG' AND f.termination_date IS NULL GROUP BY 1) c)
WHERE id = '$JOB';
SELECT catalog.refresh_derived();
SQL
echo "job $STATUS. Next: validate (val3dity), then set validation_passed and move the layer to 'validated' (see docs/en/07_RUNBOOK.md)."
exit $RC
