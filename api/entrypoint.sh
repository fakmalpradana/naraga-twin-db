#!/bin/sh
# Runs on every start/deploy; every step is idempotent.
set -e
until pg_isready -d "$DATABASE_URL" >/dev/null 2>&1; do echo "waiting for database"; sleep 2; done
dbmate up
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q \
  -v app_pw="${APP_DB_PASSWORD:?APP_DB_PASSWORD required}" -v api_pw="${API_DB_PASSWORD:?API_DB_PASSWORD required}" \
  -f /srv/db/bootstrap_roles.sql
for f in /srv/db/seeds/"${PROJECT:-oikn}"/*.sql; do [ -f "$f" ] && psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -f "$f" >/dev/null; done
python manage.py migrate --noinput
python manage.py collectstatic --noinput >/dev/null
python manage.py bootstrap_access
exec gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 2 --access-logfile -
