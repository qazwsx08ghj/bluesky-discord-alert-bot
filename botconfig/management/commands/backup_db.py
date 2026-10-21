"""Copy the database to backups/ before it is changed.

    python manage.py backup_db --if-pending   # only when migrations are waiting (start.bat)
    python manage.py backup_db                # always

Uses SQLite's backup API, so the copy is consistent even while the bot is running.
Automatic backups keep the newest KEEP files. Backups contain your settings, including
passwords: the backups folder is private and never committed to Git.
"""
import sqlite3
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.executor import MigrationExecutor

PREFIX = "db-auto-"
KEEP = 5


def pending_migrations(alias=DEFAULT_DB_ALIAS) -> list:
    executor = MigrationExecutor(connections[alias])
    return executor.migration_plan(executor.loader.graph.leaf_nodes())


class Command(BaseCommand):
    help = "Back up the database into the backups folder."

    def add_arguments(self, parser):
        parser.add_argument("--if-pending", action="store_true", help="Only back up if migrations are waiting.")
        parser.add_argument("--dir", default=None, help="Folder for backups (default: backups/ in the project).")

    def handle(self, *args, if_pending=False, dir=None, **options):
        source = Path(settings.DATABASES["default"]["NAME"])
        if not source.exists():
            self.stdout.write("No database yet; nothing to back up.")
            return
        if if_pending and not pending_migrations():
            self.stdout.write("The database is up to date; no backup needed.")
            return
        folder = Path(dir) if dir else Path(settings.BASE_DIR) / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{PREFIX}{datetime.now():%Y%m%d-%H%M%S-%f}.sqlite3"
        src, dst = sqlite3.connect(str(source)), sqlite3.connect(str(target))
        try:
            src.backup(dst)
            check = dst.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dst.close()
            src.close()
        if check != "ok":
            target.unlink(missing_ok=True)
            raise SystemExit("The backup copy failed its integrity check, so the database was not changed.")
        for old in sorted(folder.glob(f"{PREFIX}*.sqlite3"))[:-KEEP]:
            old.unlink()
        self.stdout.write(f"Database backed up to {target}")
