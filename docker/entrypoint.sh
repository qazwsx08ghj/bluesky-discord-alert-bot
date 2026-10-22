#!/bin/sh
# Starts one part of the bot inside its container. See docs/DOCKER.md.
#
#   web         prepare the data (secret key, backup before migrations, migrations), then the control panel;
#               a warning page instead if a newer version updated the data (botconfig/compat.py)
#   checker     wait until the database is ready (and not from a newer version), then the checker
#   import-old  setup.bat: bring over the data of an earlier install mounted at /import (read-only)
#   backup      update.bat / backup.bat: copy the database (backup_db options follow)
#   backups     restore.bat: copy every backup in the volume to /export (existing files are kept)
#   restore     restore.bat: put a backup back (the bot must be stopped); the file follows
#   anything else is run as a command, e.g. "python manage.py check"
set -eu
cd /app

case "${1:-web}" in
  web)
    # Exit code 3: a newer version of the bot updated the data last (botconfig/compat.py).
    # Nothing was backed up, migrated or changed; the panel shows a warning page instead.
    status=0
    python manage.py prepare_data || status=$?
    if [ "$status" -eq 3 ]; then
      export ALERTBOT_BLOCKED=newer_data
    elif [ "$status" -ne 0 ]; then
      exit "$status"
    fi
    exec gunicorn config.wsgi:application \
      --bind 0.0.0.0:8000 \
      --workers "${WEB_WORKERS:-2}" \
      --threads 4 \
      --timeout 120 \
      --graceful-timeout 20 \
      --access-logfile - \
      --error-logfile -
    ;;
  checker)
    python manage.py wait_for_database
    exec python manage.py run_scheduler
    ;;
  import-old)
    shift
    exec python manage.py prepare_data --import-from /import "$@"
    ;;
  backup)
    shift
    exec python manage.py backup_db "$@"
    ;;
  backups)
    mkdir -p /data/backups
    for f in /data/backups/*.sqlite3; do
      [ -e "$f" ] || continue
      [ -e "/export/$(basename "$f")" ] || cp "$f" /export/
    done
    ;;
  restore)
    shift
    exec python manage.py restore_db "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
