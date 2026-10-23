"""Never run an older version of the bot on data that a newer version has updated.

Each version knows its own database migrations. If the database has a migration of one of
this version's apps that this version doesn't have, a newer version updated the data last.
This version can't read that data correctly, and must never back it up over, migrate or
change it. It refuses to run instead, and says what to do.

Where it is checked (1.0.0-rc.2 and later; older versions don't have this check):
- the web service's start (prepare_data), before the backup and the migrations: the panel
  then shows a warning page instead (config/compat_middleware.py) and nothing touches the data;
- the scripts (check_compat), before they replace a running bot with this folder's version;
- the checker's start (wait_for_database) and every scheduled check (scheduler.run_cycle).
"""
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder

NEWER_DATA_EXIT = 3  # exit code of prepare_data and check_compat; the scripts and entrypoint.sh look for it
BLOCKED_NEWER_DATA = "newer_data"  # settings.ALERTBOT_BLOCKED while the web service shows the warning page

TITLE = "This is an older version of the bot than the one that last used your data"
MESSAGE = ("This older version won't run, so it can't change or damage that data.\n"
           "- To keep using the newer version: double-click start.bat in the folder of your newest download.\n"
           "- To go back to this older version on purpose: double-click restore.bat in this older version's "
           "folder and choose a backup made before the update (its name starts with db-before-update).")

_known = None


def known_migrations() -> set:
    """Every migration this version has (and the ones its squashed migrations replace)."""
    global _known
    if _known is None:
        loader = MigrationLoader(None, ignore_no_migrations=True)
        known = set(loader.disk_migrations)
        for migration in loader.disk_migrations.values():
            known.update(tuple(key) for key in (migration.replaces or []))
        _known = known
    return _known


def unknown_migrations(using=DEFAULT_DB_ALIAS) -> list:
    """Migrations in the database, of apps this version has, that this version doesn't know.

    Empty when the data is from this version or an older one (or there is no database yet).
    Apps this version doesn't have (such as Django's admin, removed in 1.0.0) are ignored.
    Only reads.
    """
    connection = connections[using]
    name = settings.DATABASES[using]["NAME"]
    if connection.vendor == "sqlite" and str(name) != ":memory:" and "mode=memory" not in str(name) \
            and not Path(name).exists() and not connection.connection:
        return []  # no database yet: don't create an empty file just by looking
    recorder = MigrationRecorder(connection)
    if not recorder.has_table():
        return []
    labels = {config.label for config in apps.get_app_configs()}
    known = known_migrations()
    return sorted(f"{app}.{migration}" for app, migration in recorder.applied_migrations()
                  if app in labels and (app, migration) not in known)


def unknown_in(applied) -> list:
    """Like unknown_migrations(), for the (app, name) pairs recorded in a database copy."""
    labels = {config.label for config in apps.get_app_configs()}
    known = known_migrations()
    return sorted(f"{app}.{name}" for app, name in applied if app in labels and (app, name) not in known)


def explanation(found) -> str:
    return f"{TITLE}.\n{MESSAGE}\n(Not known to this version: {', '.join(found)})"
