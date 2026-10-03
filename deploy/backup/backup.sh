#!/bin/sh
# Nightly dump to an S3-compatible bucket. Env: DATABASE_URL, BACKUP_BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
# optional AWS_ENDPOINT_URL (non-AWS providers), BACKUP_KEEP_DAYS (default 30, enforce with a bucket lifecycle rule).
set -eu
f="/tmp/twin-$(date -u +%Y%m%dT%H%M%SZ).dump"
pg_dump -Fc --no-owner "$DATABASE_URL" -f "$f"
echo "dump $(du -h "$f" | cut -f1)"
if [ -n "${BACKUP_BUCKET:-}" ]; then
  aws s3 cp "$f" "s3://$BACKUP_BUCKET/$(basename "$f")" ${AWS_ENDPOINT_URL:+--endpoint-url "$AWS_ENDPOINT_URL"}
else
  echo "BACKUP_BUCKET not set: dump kept only in the container (test run)"
fi
