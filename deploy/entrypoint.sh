#!/bin/sh
# Starts the website. Structure changes (migrations) and the website's database rights are applied as the
# owner account; the website itself then runs as the limited account.
set -e
if [ -n "$POSTGRES_OWNER_USER" ]; then
  HMS_USE_OWNER_DB=true python manage.py migrate --noinput
  HMS_USE_OWNER_DB=true python manage.py apply_db_grants
else
  python manage.py migrate --noinput
fi
if [ "$HMS_MODE" = "cloud" ]; then
  HMS_USE_OWNER_DB=true python manage.py prepare_cloud
fi
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers "${WEB_WORKERS:-3}" --access-logfile -
