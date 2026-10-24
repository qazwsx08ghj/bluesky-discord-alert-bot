"""FINGERPRINT=<code> of what counts in the bot's data (botconfig/datastate.fingerprint): what you set
up, the posts noticed and the notifications finished. update.bat compares it before and after
starting a new version: unchanged means nothing of value was written, so going back is safe.
Only reads (a consistent copy, through SQLite's backup API, removed afterwards)."""
import sqlite3
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from botconfig import datastate
from botconfig.sqlite_files import remove_journal_files, standalone_copy


class Command(BaseCommand):
    help = "Print a fingerprint of the bot's data (read-only)."

    def handle(self, *args, **options):
        db = Path(settings.DATABASES["default"]["NAME"])
        if not db.exists():
            raise CommandError("There is no database.")
        copy = db.with_name(db.name + ".fingerprint")
        try:
            live = sqlite3.connect(str(db))
            try:
                check = standalone_copy(live, copy)
            finally:
                live.close()
            if check != "ok":
                raise CommandError(f"The data couldn't be read ({check[:200]}).")
            self.stdout.write(f"FINGERPRINT={datastate.fingerprint(datastate.snapshot(copy))}")
        finally:
            copy.unlink(missing_ok=True)
            remove_journal_files(copy)
