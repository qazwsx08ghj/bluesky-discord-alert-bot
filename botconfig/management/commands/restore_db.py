"""Put a backup back in place of the bot's database (used by restore.bat).

    python manage.py restore_db /restore/db-before-update-20261021-120000.sqlite3

The bot must be stopped first (restore.bat stops it, and has already made a backup of the
current data). In order, stopping at the first problem so the database is never left
half-replaced:
1. The backup is copied to a scratch folder (the original is only read) and checked:
   it must open, pass SQLite's integrity check and be this bot's database.
2. The current database's journal files (-wal, -shm) are removed, so they can't be
   replayed onto the restored file.
3. The checked copy replaces the database in one step.
Migrations are applied by the normal start afterwards (with a backup first, if needed).
"""
import os
import sqlite3
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from .prepare_data import copy_database


def looks_like_a_bot_database(path: Path) -> bool:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    finally:
        con.close()
    return "django_migrations" in tables


class Command(BaseCommand):
    help = "Replace the bot's database with a backup. Stop the bot first."

    def add_arguments(self, parser):
        parser.add_argument("backup", help="The backup file (.sqlite3) to put back.")

    def handle(self, *args, backup, **options):
        source = Path(backup)
        if not source.is_file() or source.suffix != ".sqlite3":
            raise CommandError(f"There is no backup called {source.name}.")
        db = Path(settings.DATABASES["default"]["NAME"])
        staged = db.with_name(db.name + ".restore-check")
        try:
            copy_database(source, staged)  # scratch copy + integrity check; the source is only read
        except SystemExit as exc:
            raise CommandError(f"{source.name} is damaged, so it wasn't restored. Your data is unchanged.") from exc
        if not looks_like_a_bot_database(staged):
            staged.unlink(missing_ok=True)
            raise CommandError(f"{source.name} isn't a backup of this bot, so it wasn't restored. "
                               "Your data is unchanged.")
        for suffix in ("-wal", "-shm"):
            Path(f"{db}{suffix}").unlink(missing_ok=True)
        os.replace(staged, db)
        self.stdout.write(f"Restored {source.name}.")
