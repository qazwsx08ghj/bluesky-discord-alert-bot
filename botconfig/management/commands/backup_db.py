"""Copy the database to the backups folder before it is changed.

    python manage.py backup_db --if-pending                # only when migrations are waiting (start-up)
    python manage.py backup_db                             # always
    python manage.py backup_db --label before-update       # named copy (update.bat), kept separately
    python manage.py backup_db --export /export            # also copy it there (Docker: a Windows folder)

Uses SQLite's backup API, so the copy is consistent even while the bot is running.
Every backup is checked before it counts:
- it is one self-contained file (no journal beside it), written under a temporary name and
  renamed only after SQLite's integrity check passed;
- the copy in --export is compared with the backup byte for byte (SHA-256) before it gets
  its final name, so a half-written copy in the Windows folder never looks like a backup.

Which backups are kept (rotate): of each kind (db-auto-, db-before-update-, ...), the newest
KEEP; and, older than those, the newest backup of each earlier database version (up to
KEEP_EARLIER_VERSIONS). So the backup taken just before an update that changed the database
stays, however often update.bat or backup.bat runs afterwards: it is the one an older
version of the bot can go back to.

Backups contain your settings, including passwords: the backups folder is private and
never committed to Git.
Folder: --dir, else BACKUP_DIR (Docker: /data/backups in the data volume), else backups/.
"""
import hashlib
import os
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.executor import MigrationExecutor

from botconfig.sqlite_files import applied_migrations, remove_journal_files, sha256, standalone_copy

PREFIX = "db-auto-"
KEEP = 5
KEEP_EARLIER_VERSIONS = 5


def pending_migrations(alias=DEFAULT_DB_ALIAS) -> list:
    executor = MigrationExecutor(connections[alias])
    return executor.migration_plan(executor.loader.graph.leaf_nodes())


def backup(folder: Path, prefix: str = PREFIX, keep: int = KEEP) -> Path:
    """Write a checked, self-contained copy of the database into folder. Raises SystemExit if it is bad."""
    source = Path(settings.DATABASES["default"]["NAME"])
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{prefix}{datetime.now():%Y%m%d-%H%M%S-%f}.sqlite3"
    partial = target.with_name(target.name + ".partial")
    src = sqlite3.connect(str(source))
    try:
        check = standalone_copy(src, partial)
    finally:
        src.close()
    if check != "ok":
        partial.unlink(missing_ok=True)
        raise SystemExit("The backup copy failed its integrity check, so the database was not changed.")
    os.replace(partial, target)
    remove_journal_files(target)
    rotate(folder, prefix, keep)
    return target


def schema_signature(path: Path) -> str | None:
    """Which database version a backup is (its recorded migrations); None if it can't be read."""
    applied = applied_migrations(path)
    if applied is None:
        return None
    return hashlib.sha256(repr(sorted(applied)).encode()).hexdigest()


def rotate(folder: Path, prefix: str, keep: int = KEEP, keep_earlier: int = KEEP_EARLIER_VERSIONS) -> list[Path]:
    """Remove old backups of one kind (see the module notes). Returns the removed files."""
    files = sorted(folder.glob(f"{prefix}*.sqlite3"))  # the names sort by the time they were made
    if len(files) <= keep:
        return []
    kept = set(files[-keep:])
    versions = {schema_signature(f) for f in kept}
    earlier = 0
    for older in reversed(files[:-keep]):  # newest first
        signature = schema_signature(older)
        if earlier < keep_earlier and signature is not None and signature not in versions:
            kept.add(older)
            versions.add(signature)
            earlier += 1
    removed = [f for f in files if f not in kept]
    for old in removed:
        old.unlink(missing_ok=True)
    return removed


def export_copy(target: Path, folder: Path) -> Path:
    """Copy a backup into folder and check the copy is identical before it gets its name."""
    folder.mkdir(parents=True, exist_ok=True)
    copy = folder / target.name
    partial = folder / (target.name + ".partial")
    shutil.copyfile(target, partial)
    if sha256(partial) != sha256(target):
        partial.unlink(missing_ok=True)
        raise SystemExit("The copy in the backups folder didn't match the backup, so it was removed. "
                         "The backup inside Docker is fine.")
    os.replace(partial, copy)
    return copy


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
        self.stdout.write(f"Database backed up to {target} (checked: complete and undamaged)")
        if export:
            export_copy(target, Path(export))
            self.stdout.write(f"Copy saved as {target.name} (checked: identical to the backup)")
