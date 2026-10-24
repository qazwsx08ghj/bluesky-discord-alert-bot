"""Can this (new) version take over the bot's data? Run by update.bat in the new image, before anything
changes. No network; only reads the live database (through SQLite's backup API, so the bot may run).

1. The data must not come from a newer version than this one (exit code 3).
2. There must be room for the backup and the update: free space at least 3 x the database + 50 MB.
3. A copy of the database is updated with this version's migrations, in a separate process, and
   then removed. If that fails, the real update would fail too (exit code 1, the reason printed).
Exit code 0: go ahead.
"""
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from botconfig import compat
from botconfig.sqlite_files import applied_migrations, remove_journal_files, standalone_copy

MARGIN = 50 * 1024 * 1024


class Command(BaseCommand):
    help = "Check that this version can update the bot's data (read-only)."

    def handle(self, *args, **options):
        db = Path(settings.DATABASES["default"]["NAME"])
        if not db.exists():
            self.stdout.write("No data yet: nothing to check.")
            return
        size = db.stat().st_size
        free = shutil.disk_usage(db.parent).free
        if free < 3 * size + MARGIN:
            raise CommandError(f"Not enough free space for the update: {free // 2**20} MB free, "
                               f"{(3 * size + MARGIN) // 2**20} MB needed. Nothing was changed.")
        copy = db.with_name(db.name + ".update-preflight")
        try:
            remove_journal_files(copy)
            live = sqlite3.connect(str(db))
            try:
                check = standalone_copy(live, copy)
            finally:
                live.close()
            if check != "ok":
                raise CommandError(f"The bot's data didn't pass SQLite's integrity check ({check[:200]}). "
                                   "Nothing was changed.")
            newer = compat.unknown_in(applied_migrations(copy) or set())
            if newer:
                raise CommandError("The data comes from a newer version than this one, so it can't take over. "
                                   f"Nothing was changed. ({', '.join(newer)})", returncode=compat.NEWER_DATA_EXIT)
            env = {**__import__("os").environ, "DATABASE_PATH": str(copy), "SCHEDULER_IN_WEB": "0"}
            res = subprocess.run([sys.executable, str(Path(settings.BASE_DIR) / "manage.py"), "migrate",
                                  "--fake-initial", "--no-input", "-v", "0"], cwd=settings.BASE_DIR, env=env,
                                 capture_output=True, text=True, timeout=900)
            if res.returncode != 0:
                raise CommandError("Updating a copy of the data failed, so the update wasn't started. Nothing was "
                                   "changed.\n" + (res.stderr or res.stdout)[-1500:])
        finally:
            copy.unlink(missing_ok=True)
            remove_journal_files(copy)
        self.stdout.write(f"Preflight passed: version {settings.APP_VERSION} can update this data.")
