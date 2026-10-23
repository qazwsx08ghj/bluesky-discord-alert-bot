"""Get the data folder ready before the bot starts (run by the Docker web service on every start).

    python manage.py prepare_data                      # every start
    python manage.py prepare_data --import-from /import  # setup.bat: bring over an old install

In order, stopping at the first problem so the database is never changed half-way:
0. If it is going to import or migrate, take the exclusive data lock (botconfig/datalock.py):
   wait until no other part of the bot is using the database, for at most
   DATA_LOCK_WAIT_SECONDS. If it stays busy, nothing is changed and it exits with code 75;
   Docker then starts the web service again.
1. Create the data folders and, once per install, a random SECRET_KEY_FILE.
2. --import-from: copy the old install's db.sqlite3 into place, but only if there is no
   database yet. The old file is only read, never changed. Its .env settings are brought
   over by the normal one-time import, which reads LEGACY_ENV_FILE (set by setup.bat).
   Then: if a newer version updated the data last, stop here with exit code 3 (see
   botconfig/compat.py): no backup, no migrations, nothing changed. entrypoint.sh then
   shows a warning page instead of the panel.
3. Back up the database if migrations are waiting (backup_db --if-pending).
4. Apply migrations (--fake-initial, as start.bat always did, for databases made by the
   very first version, whose tables had no migration records).
5. Load the settings (the one-time import happens here) and print a summary without secrets.
"""
import os
import secrets
import shutil
import sqlite3
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connections

from botconfig import datalock
from botconfig.sqlite_files import remove_journal_files, standalone_copy


def write_secret_key(path: Path) -> bool:
    """Create the key file once (only readable by the bot). Returns True if it was created."""
    if path.is_file() and path.read_text(encoding="utf-8").strip():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(secrets.token_urlsafe(50), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return True


def copy_database(source: Path, target: Path) -> None:
    """A consistent, self-contained copy, checked before it is put in place. The source is only read.

    The old folder is mounted read-only, and SQLite can't open a database in WAL mode
    there (it needs to write next to it). So the file and its -wal/-shm journal are first
    copied into a scratch folder, opened there (SQLite applies the journal), and then
    copied with the backup API into one file with no journal (sqlite_files.standalone_copy).
    Journal files left next to the target's name are removed before the copy takes its
    place, so they can never be applied to it.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = target.parent / f"{target.name}.scratch"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir()
    tmp = target.with_name(target.name + ".importing")
    try:
        for suffix in ("", "-wal", "-shm"):
            part = Path(f"{source}{suffix}")
            if part.is_file():
                shutil.copyfile(part, scratch / f"old.sqlite3{suffix}")
        try:
            src = sqlite3.connect(str(scratch / "old.sqlite3"))
        except sqlite3.Error:
            src = None
        try:
            check = standalone_copy(src, tmp) if src is not None else "unreadable"
        finally:
            if src is not None:
                src.close()
        if check != "ok":
            tmp.unlink(missing_ok=True)
            raise SystemExit("The old database is damaged (integrity check failed), so it wasn't imported.")
        remove_journal_files(target)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
        remove_journal_files(tmp)
        shutil.rmtree(scratch, ignore_errors=True)


def needs_changing(import_from, db: Path) -> bool:
    """Will prepare_data change the database as a whole (import, or migrations)? Only reads."""
    if import_from or not db.exists():
        return True  # (without opening it: that would create an empty file)
    from botconfig.management.commands.backup_db import pending_migrations

    try:
        return bool(pending_migrations())
    except Exception:  # no database yet, or unreadable: migrate decides, under the lock
        return True
    finally:
        connections.close_all()


class Command(BaseCommand):
    help = "Prepare the data folder: secret key, optional import, backup, migrations."

    def add_arguments(self, parser):
        parser.add_argument("--import-from", default=None,
                            help="Folder of an earlier install (its db.sqlite3 is copied if there is no database yet).")

    def handle(self, *args, import_from=None, **options):
        db = Path(settings.DATABASES["default"]["NAME"])
        db.parent.mkdir(parents=True, exist_ok=True)
        Path(settings.BACKUP_DIR).mkdir(parents=True, exist_ok=True)
        if os.getenv("CACHE_DIR"):
            Path(os.environ["CACHE_DIR"]).mkdir(parents=True, exist_ok=True)
        key_file = os.getenv("SECRET_KEY_FILE")
        if key_file and write_secret_key(Path(key_file)):
            self.stdout.write("Created this install's secret key.")

        lock = datalock.DataLock(exclusive=True, db_path=db)
        if needs_changing(import_from, db) and not lock.acquire(timeout=datalock.wait_seconds()):
            self.stderr.write("Another part of the bot is still using the database, so it wasn't updated "
                              "(nothing was changed). Docker starts the bot again in a moment.")
            raise SystemExit(datalock.BUSY_EXIT)
        try:
            if import_from:
                self._import(Path(import_from), db)

            from botconfig import compat

            newer = compat.unknown_migrations()
            connections.close_all()
            if newer:
                self.stderr.write(compat.explanation(newer))
                raise SystemExit(compat.NEWER_DATA_EXIT)
            call_command("backup_db", if_pending=True, stdout=self.stdout)
            call_command("migrate", fake_initial=True, interactive=False, verbosity=0)
            connections.close_all()
        finally:
            lock.release()
        self.stdout.write("The database is up to date.")

        from botconfig import store
        from bluesky_monitor.models import MonitoredAccount
        from discord_alerts.models import DiscordWebhook

        row = store.get()  # imports the old .env settings once, if there are none yet
        handle, password = store.credentials()
        self.stdout.write(
            f"Ready: {MonitoredAccount.objects.count()} account(s), {DiscordWebhook.objects.count()} channel(s), "
            f"Bluesky login {'saved' if handle and password else 'not set up yet'}, "
            f"checking every {row.poll_interval_minutes} minute(s).")

    def _import(self, folder: Path, db: Path):
        old_db = folder / "db.sqlite3"
        if db.exists() and db.stat().st_size > 0:
            self.stdout.write("This install already has data, so nothing was imported (your data is unchanged).")
            return
        if old_db.is_file():
            copy_database(old_db, db)
            self.stdout.write("Copied the accounts, channels and history from the earlier version.")
        else:
            self.stdout.write("No database from an earlier version was found.")
        if (folder / ".env").is_file():
            self.stdout.write("The settings from the earlier version's .env are brought over if none are saved yet.")
