"""Put a backup back in place of the bot's database (used by restore.bat).

    python manage.py restore_db /restore/db-before-update-20261021-120000.sqlite3
    python manage.py restore_db --report /restore/db-before-update-20261021-120000.sqlite3

--report changes nothing (the bot may be running): it says what putting this backup back
would undo, compared with the data now (botconfig/datastate.py). Exit code 0: nothing you
set up and no history would be lost; 10: something would be (restore.bat then asks for
RESTORE to be typed). To compare like with like, a checked copy of the backup is first
brought up to this version's database layout (the migrations a start would apply anyway).

Putting the backup back needs the bot stopped (restore.bat stops it, and has already made a
backup of the current data). In order, stopping at the first problem so the database is
never left half-replaced:
1. The backup is copied to a scratch file (the original is only read) and checked: it must
   open, pass SQLite's integrity check and be this bot's database, and must not come from a
   newer version of the bot (exit code 3). The copy is one self-contained file.
2. The exclusive data lock is taken (botconfig/datalock.py): if any part of the bot is still
   running, nothing is changed.
3. The current database's journal files (-wal, -shm) are removed, so they can't be applied
   to the restored file, and the checked copy replaces the database in one step.
No file other than the database is left behind. Migrations are applied by the normal start
afterwards (with a backup first, if needed).
"""
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from botconfig import compat, datalock, datastate
from botconfig.sqlite_files import applied_migrations, make_standalone, remove_journal_files, standalone_copy

from .prepare_data import copy_database

LOSES_DATA_EXIT = 10
RESTORE_LOCK_WAIT_SECONDS = 15


def looks_like_a_bot_database(path: Path) -> bool:
    return applied_migrations(path) is not None


def _discard(path: Path):
    path.unlink(missing_ok=True)
    remove_journal_files(path)


class Command(BaseCommand):
    help = "Replace the bot's database with a backup (stop the bot first), or --report what that would undo."

    def add_arguments(self, parser):
        parser.add_argument("backup", help="The backup file (.sqlite3) to put back.")
        parser.add_argument("--report", action="store_true",
                            help="Change nothing; say what restoring this backup would undo.")

    def handle(self, *args, backup, report=False, **options):
        source = Path(backup)
        if not source.is_file() or source.suffix != ".sqlite3":
            raise CommandError(f"There is no backup called {source.name}.")
        db = Path(settings.DATABASES["default"]["NAME"])
        staged = db.with_name(db.name + (".restore-report" if report else ".restore-check"))
        try:
            self._check(source, staged)
            if report:
                return self._report(source, staged, db)
            self._put_back(source, staged, db)
        finally:
            _discard(staged)

    def _check(self, source: Path, staged: Path):
        try:
            copy_database(source, staged)  # scratch copy + integrity check; the source is only read
        except SystemExit as exc:
            raise CommandError(f"{source.name} is damaged, so it wasn't restored. Your data is unchanged.") from exc
        applied = applied_migrations(staged)
        if applied is None:
            raise CommandError(f"{source.name} isn't a backup of this bot, so it wasn't restored. "
                               "Your data is unchanged.")
        newer = compat.unknown_in(applied)
        if newer:
            raise CommandError(f"{source.name} was made by a newer version of the bot, which this version can't "
                               f"use, so it wasn't restored. Your data is unchanged. (Not known to this version: "
                               f"{', '.join(newer)})", returncode=compat.NEWER_DATA_EXIT)

    def _put_back(self, source: Path, staged: Path, db: Path):
        lock = datalock.DataLock(exclusive=True, db_path=db)
        if not lock.acquire(timeout=RESTORE_LOCK_WAIT_SECONDS):
            raise CommandError("The bot is still running (or another restore or update is), so the backup wasn't "
                               "put back. Your data is unchanged. Stop the bot, then try again.")
        try:
            remove_journal_files(db)
            os.replace(staged, db)
        finally:
            lock.release()
        self.stdout.write(f"Restored {source.name}.")

    def _report(self, source: Path, staged: Path, db: Path):
        # The backup, brought up to this version's layout in a separate process (the same
        # migrations a start applies), so it is compared like with like.
        env = {**os.environ, "DATABASE_PATH": str(staged), "SCHEDULER_IN_WEB": "0"}
        res = subprocess.run([sys.executable, str(Path(settings.BASE_DIR) / "manage.py"), "migrate",
                              "--fake-initial", "--no-input", "-v", "0"], cwd=settings.BASE_DIR, env=env,
                             capture_output=True, text=True, timeout=600)
        if res.returncode != 0:
            raise CommandError(f"{source.name} couldn't be brought up to this version of the bot, so restoring it "
                               "would fail. Your data is unchanged.\n" + (res.stderr or res.stdout)[-1500:])
        make_standalone(staged)

        current = db.with_name(db.name + ".restore-now")
        _discard(current)
        try:
            import sqlite3

            if db.exists():
                live = sqlite3.connect(str(db))
                try:
                    check = standalone_copy(live, current)  # a consistent copy, even while the bot runs
                finally:
                    live.close()
                if check != "ok":
                    raise CommandError("The bot's current data couldn't be read for the comparison, so nothing was "
                                       "changed. (Its integrity check said: " + check[:200] + ")")
                now = datastate.snapshot(current)
            else:
                now = datastate.snapshot(staged) | {"activity": []}
            impact = datastate.compare(now, datastate.snapshot(staged))
        finally:
            _discard(current)

        lines = datastate.describe(impact)
        if lines:
            self.stdout.write("Putting this backup back would undo what changed since it was made:")
            for line in lines:
                self.stdout.write(f"  - {line}.")
        else:
            self.stdout.write("Nothing you set up and no notification history would be lost: "
                              "the data now matches this backup.")
        if impact.activity:
            self.stdout.write(f"  ({impact.activity} entr{'y' if impact.activity == 1 else 'ies'} in the activity "
                              "list since the backup would be removed.)")
        if impact.sending_now:
            self.stdout.write(f"  ({impact.sending_now} notification(s) are being sent right now.)")
        if impact.loses_data:
            raise SystemExit(LOSES_DATA_EXIT)
