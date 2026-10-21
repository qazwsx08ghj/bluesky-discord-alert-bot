"""Copy the database to the backups folder before it is changed.

    python manage.py backup_db --if-pending                # only when migrations are waiting (start-up)
    python manage.py backup_db                             # always
    python manage.py backup_db --label before-update       # named copy (update.bat), kept separately
    python manage.py backup_db --export /export            # also copy it there (Docker: a Windows folder)

Uses SQLite's backup API, so the copy is consistent even while the bot is running.
Each kind of backup keeps its newest KEEP files. Backups contain your settings, including
passwords: the backups folder is private and never committed to Git.
Folder: --dir, else BACKUP_DIR (Docker: /data/backups in the data volume), else backups/.
"""
import re
import shutil
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


def backup(folder: Path, prefix: str = PREFIX, keep: int = KEEP) -> Path:
    """Write a checked copy of the database into folder. Raises SystemExit if the copy is bad."""
    source = Path(settings.DATABASES["default"]["NAME"])
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{prefix}{datetime.now():%Y%m%d-%H%M%S-%f}.sqlite3"
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
    for old in sorted(folder.glob(f"{prefix}*.sqlite3"))[:-keep]:
        old.unlink()
    return target


class Command(BaseCommand):
    help = "Back up the database into the backups folder."

    def add_arguments(self, parser):
        parser.add_argument("--if-pending", action="store_true", help="Only back up if migrations are waiting.")
        parser.add_argument("--dir", default=None, help="Folder for backups (default: BACKUP_DIR).")
        parser.add_argument("--label", default="", help="Name this backup, e.g. before-update (letters, digits, -).")
        parser.add_argument("--export", default=None, help="Also copy the backup into this folder.")

    def handle(self, *args, if_pending=False, dir=None, label="", export=None, **options):
        source = Path(settings.DATABASES["default"]["NAME"])
        if not source.exists():
            self.stdout.write("No database yet; nothing to back up.")
            return
        if if_pending and not pending_migrations():
            self.stdout.write("The database is up to date; no backup needed.")
            return
        if label and not re.fullmatch(r"[A-Za-z0-9-]{1,40}", label):
            raise SystemExit("--label may only contain letters, digits and '-'.")
        prefix = f"db-{label}-" if label else PREFIX
        target = backup(Path(dir) if dir else Path(settings.BACKUP_DIR), prefix=prefix)
        self.stdout.write(f"Database backed up to {target}")
        if export:
            folder = Path(export)
            folder.mkdir(parents=True, exist_ok=True)
            copy = folder / target.name
            shutil.copyfile(target, copy)
            self.stdout.write(f"Copy saved as {target.name}")
