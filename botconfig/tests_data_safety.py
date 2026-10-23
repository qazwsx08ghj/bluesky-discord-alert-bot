"""Data safety: the data lock, SQLite journal files, verified backups and the restore report.

- The data lock (datalock.py): a migration or restore waits for (or refuses while) any part of
  the bot uses the data; a lock never outlives its process. Real processes; Linux and macOS
  only (flock). The Docker tests (config/tests_docker_safety.py) repeat this across containers.
- Journal files (sqlite_files.py): copies are self-contained, a journal is never applied to a
  file it doesn't belong to, and nothing is left behind.
- Backups (backup_db): checked before they count; the export is compared with the backup;
  the backup taken before an update that changed the database is never rotated away.
- The restore report (restore_db --report): what a restore would undo, in plain words, with
  exit code 10 when something you set up or any history would be lost. Secrets never shown.
"""
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase

from botconfig import datalock, datastate
from botconfig.management.commands import backup_db
from botconfig.sqlite_files import journal_files, open_read_only
from .tests_step8 import DataFolderTestCase, sha

ROOT = Path(settings.BASE_DIR)
FAKE_HOOK = "https://discord.com/api/webhooks/1/fake-report-token"
POSIX = unittest.skipIf(sys.platform == "win32", "flock: Linux and macOS (the bot runs on Linux in Docker)")

HOLDER = """
import sys, time
sys.path.insert(0, sys.argv[1])
from botconfig.datalock import DataLock
lock = DataLock(exclusive=sys.argv[3] == "exclusive", db_path=sys.argv[2])
assert lock.acquire(timeout=10)
print("held", flush=True)
time.sleep(600)
"""


def make_wal_database(path: Path, rows, keep_open=False):
    """A database in WAL mode whose last rows are still only in its -wal journal."""
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("CREATE TABLE IF NOT EXISTS marker (v TEXT)")
    for value in rows:
        con.execute("INSERT INTO marker VALUES (?)", (value,))
    con.commit()
    if keep_open:
        return con
    wal = Path(f"{path}-wal").read_bytes()
    con.close()
    return wal


class DataLockTestCase(DataFolderTestCase):
    def hold(self, mode="shared"):
        """Another process holding the data lock, like a running checker (or a migration)."""
        self.data.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen([sys.executable, "-c", HOLDER, str(ROOT), str(self.db), mode],
                                stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        self.assertEqual(proc.stdout.readline().strip(), "held")
        return proc


@POSIX
class DataLockTests(DataLockTestCase):
    def test_many_parts_of_the_bot_can_run_together(self):
        self.hold("shared")
        lock = datalock.DataLock(exclusive=False, db_path=self.db)
        self.assertTrue(lock.acquire(timeout=1))
        lock.release()

    def test_a_migration_or_restore_waits_and_then_gives_up(self):
        self.hold("shared")
        lock = datalock.DataLock(exclusive=True, db_path=self.db)
        began = time.monotonic()
        self.assertFalse(lock.acquire(timeout=0.6))
        self.assertLess(time.monotonic() - began, 3, "the wait is limited")
        self.assertFalse(lock.held)

    def test_nothing_of_the_bot_starts_during_a_migration_or_restore(self):
        self.hold("exclusive")
        self.assertFalse(datalock.DataLock(exclusive=False, db_path=self.db).acquire(timeout=0.3))

    def test_a_killed_process_leaves_no_stale_lock(self):
        proc = self.hold("shared")
        proc.send_signal(signal.SIGKILL)
        proc.wait()
        self.assertTrue(datalock.lock_file(self.db).exists(), "the file stays; it is never the lock itself")
        lock = datalock.DataLock(exclusive=True, db_path=self.db)
        self.assertTrue(lock.acquire(timeout=1), "the kernel dropped the lock with the process")
        lock.release()

    def test_a_migration_waits_for_the_checker_and_changes_nothing_if_it_stays(self):
        self.manage("prepare_data")
        self.manage("migrate", "bluesky_monitor", "0005")  # an update is waiting
        before = sha(self.db)
        self.hold("shared")
        res = self.manage("prepare_data", ok=False, env={"DATA_LOCK_WAIT_SECONDS": "1"})
        self.assertEqual(res.returncode, datalock.BUSY_EXIT, res.stdout + res.stderr)
        self.assertIn("Another part of the bot is still using the database", res.stderr)
        self.assertEqual(sha(self.db), before, "no backup and no migration while the checker runs")
        self.assertEqual(self.auto_backups(), [])

    def test_a_migration_goes_ahead_once_the_checker_has_stopped(self):
        self.manage("prepare_data")
        self.manage("migrate", "bluesky_monitor", "0005")
        holder = self.hold("shared")
        later = subprocess.Popen([sys.executable, str(ROOT / "manage.py"), "prepare_data"], cwd=ROOT,
                                 env={**self.env, "DATA_LOCK_WAIT_SECONDS": "30"},
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(1.5)
        self.assertIsNone(later.poll(), "it waits")
        holder.kill()
        out, err = later.communicate(timeout=120)
        self.assertEqual(later.returncode, 0, out + err)
        self.assertIn("The database is up to date.", out)

    def test_a_restart_without_migrations_doesnt_wait_for_the_checker(self):
        self.manage("prepare_data")
        self.hold("shared")
        began = time.monotonic()
        self.manage("prepare_data", env={"DATA_LOCK_WAIT_SECONDS": "30"})
        self.assertLess(time.monotonic() - began, 25)

    def test_a_restore_is_refused_while_the_bot_runs(self):
        self.manage("prepare_data")
        self.manage("backup_db", "--label", "manual")
        [backup] = self.backups.glob("db-manual-*.sqlite3")
        self.shell("from bluesky_monitor.models import MonitoredAccount; "
                   "MonitoredAccount.objects.create(handle='added.bsky.social')")
        before = sha(self.db)
        holder = self.hold("shared")
        res = self.manage("restore_db", str(backup), ok=False)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("The bot is still running", res.stderr)
        self.assertEqual(sha(self.db), before)
        self.assertEqual(sorted(p.name for p in self.data.iterdir() if "restore" in p.name), [], "nothing left")
        holder.kill()
        holder.wait()
        self.manage("restore_db", str(backup))
        self.assertNotIn("added.bsky.social", self.shell(
            "from bluesky_monitor.models import MonitoredAccount; print(list(MonitoredAccount.objects.all()))"))

    def test_the_checker_waits_while_the_data_is_being_changed(self):
        self.manage("prepare_data")
        self.hold("exclusive")
        res = self.manage("shell", "-c", "from bluesky_monitor import scheduler; scheduler.hold_data_lock()",
                          ok=False, env={"DATA_LOCK_WAIT_SECONDS": "1"})
        self.assertEqual(res.returncode, datalock.BUSY_EXIT, res.stdout + res.stderr)

    def test_the_checker_doesnt_start_on_a_database_that_isnt_updated(self):
        self.manage("prepare_data")
        self.manage("migrate", "bluesky_monitor", "0005")
        res = self.manage("shell", "-c", "from bluesky_monitor import scheduler; scheduler.hold_data_lock()",
                          ok=False, env={"DATA_LOCK_WAIT_SECONDS": "1"})
        self.assertEqual(res.returncode, datalock.BUSY_EXIT, res.stdout + res.stderr)

    def test_the_web_service_takes_the_shared_lock_before_it_serves(self):
        from config import gunicorn_conf

        self.data.mkdir(parents=True, exist_ok=True)
        with mock.patch.dict(os.environ, {"DATABASE_PATH": str(self.db), "DATA_LOCK_WAIT_SECONDS": "0.3"}), \
                mock.patch.object(datalock, "database_path", return_value=self.db):
            gunicorn_conf.on_starting(server=None)
            self.addCleanup(gunicorn_conf._data_lock.release)
            self.assertTrue(gunicorn_conf._data_lock.held)
            self.assertFalse(datalock.DataLock(exclusive=True, db_path=self.db).acquire(timeout=0.2))


class DataLockWithoutFlockTests(SimpleTestCase):
    def test_without_flock_the_lock_does_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(datalock, "fcntl", None):
            lock = datalock.DataLock(exclusive=True, db_path=Path(tmp) / "db.sqlite3")
            self.assertFalse(lock.supported)
            self.assertTrue(lock.acquire(timeout=0))
            self.assertTrue(lock.held)
            lock.release()
            self.assertFalse(lock.held)
            self.assertFalse((Path(tmp) / datalock.DATA_LOCK_FILE_NAME).exists())


class JournalFileTests(DataFolderTestCase):
    """No journal file is ever applied to a file it doesn't belong to, and none is left behind."""

    def setUp(self):
        super().setUp()
        self.manage("prepare_data")
        self.shell("from bluesky_monitor.models import MonitoredAccount; "
                   "MonitoredAccount.objects.create(handle='in.backup.bsky.social')")
        self.manage("backup_db", "--label", "manual")
        [self.backup] = self.backups.glob("db-manual-*.sqlite3")

    def handles(self):
        with closing(open_read_only(self.db)) if not journal_files(self.db) else closing(sqlite3.connect(self.db)) as c:
            return sorted(r[0] for r in c.execute("SELECT handle FROM bluesky_monitor_monitoredaccount"))

    def leftovers(self):
        allowed = {"db.sqlite3", "backups", "secret_key", "cache", datalock.DATA_LOCK_FILE_NAME}
        return sorted(p.name for p in self.data.iterdir() if p.name not in allowed)

    def test_a_restore_leaves_nothing_behind(self):
        self.manage("restore_db", str(self.backup))
        self.assertEqual(self.leftovers(), [], "no restore-check copy and no journal files")
        self.assertEqual(self.handles(), ["in.backup.bsky.social"])

    def test_a_stale_journal_from_another_database_is_never_applied_to_the_restored_file(self):
        other = self.tmp / "other.sqlite3"
        wal = make_wal_database(other, ["from another database"] * 50)
        self.assertGreater(len(wal), 0)
        for name in ("db.sqlite3.restore-check-wal", "db.sqlite3.restore-check.importing-wal"):
            (self.data / name).write_bytes(wal)
        self.manage("restore_db", str(self.backup))
        self.assertEqual(self.handles(), ["in.backup.bsky.social"])
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
        self.assertNotIn("marker", tables, "nothing from the other database got in")
        self.assertEqual(self.leftovers(), [])

    def test_the_replaced_databases_unsaved_journal_is_thrown_away(self):
        self.shell("from bluesky_monitor.models import MonitoredAccount; "
                   "MonitoredAccount.objects.create(handle='after.backup.bsky.social')")
        writer = sqlite3.connect(self.db)  # the database's newest change is only in its journal
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO bluesky_monitor_monitoredaccount (handle, display_name, is_active, "
                       "first_poll_done, created_at, check_lock_token) VALUES ('only.in.journal', '', 1, 0, "
                       "'2026-01-01', '')")
        writer.commit()
        journal = Path(f"{self.db}-wal").read_bytes()
        writer.close()
        Path(f"{self.db}-wal").write_bytes(journal)  # as if the bot had been killed
        self.manage("restore_db", str(self.backup))
        self.assertEqual(self.handles(), ["in.backup.bsky.social"])

    def test_backups_are_one_self_contained_file(self):
        self.assertEqual(journal_files(self.backup), [])
        self.assertEqual(self.backup.read_bytes()[18:20], b"\x01\x01", "rollback-journal mode, not WAL")
        with closing(open_read_only(self.backup)) as con:
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(journal_files(self.backup), [], "reading it creates nothing beside it")
        self.assertEqual([p.name for p in self.backups.iterdir() if not p.name.endswith(".sqlite3")], [])

    def test_an_import_leaves_nothing_behind(self):
        old = self.tmp / "old install"
        old.mkdir()
        shutil.copyfile(self.backup, old / "db.sqlite3")
        fresh = self.tmp / "fresh"
        env = {"DATABASE_PATH": str(fresh / "db.sqlite3"), "BACKUP_DIR": str(fresh / "backups"),
               "SECRET_KEY_FILE": str(fresh / "secret_key"), "CACHE_DIR": str(fresh / "cache")}
        self.manage("prepare_data", "--import-from", str(old), env=env)
        self.assertEqual(sorted(p.name for p in fresh.iterdir() if p.name not in {
            "db.sqlite3", "db.sqlite3-wal", "db.sqlite3-shm", "backups", "secret_key", "cache",
            datalock.DATA_LOCK_FILE_NAME}), [])

    def test_a_report_leaves_nothing_behind_and_changes_nothing(self):
        before = sha(self.db)
        self.manage("restore_db", "--report", str(self.backup))
        self.assertEqual(sha(self.db), before)
        self.assertEqual([n for n in self.leftovers() if not n.startswith("db.sqlite3-")], [])


class VerifiedBackupTests(DataFolderTestCase):
    def setUp(self):
        super().setUp()
        self.manage("prepare_data")

    def test_a_backup_and_its_export_are_checked(self):
        export = self.tmp / "windows backups"
        out = self.manage("backup_db", "--label", "before-update", "--export", str(export)).stdout
        self.assertIn("(checked: complete and undamaged)", out)
        self.assertIn("(checked: identical to the backup)", out)
        [backup] = self.backups.glob("db-before-update-*.sqlite3")
        self.assertEqual(sha(export / backup.name), sha(backup))

    def test_an_export_that_doesnt_match_is_removed(self):
        target = self.tmp / "db-manual-1.sqlite3"
        target.write_bytes(b"the real backup" * 100)
        export = self.tmp / "export"

        def broken_copy(src, dst):
            Path(dst).write_bytes(b"cut off")

        with mock.patch.object(backup_db.shutil, "copyfile", broken_copy), self.assertRaises(SystemExit):
            backup_db.export_copy(target, export)
        self.assertEqual(list(export.iterdir()), [], "no half-written copy that looks like a backup")

    def test_a_backup_that_fails_its_check_leaves_nothing(self):
        with mock.patch.object(backup_db, "standalone_copy", return_value="*** page 3 is damaged"), \
                mock.patch.object(settings, "DATABASES", {"default": {**settings.DATABASES["default"],
                                                                      "NAME": str(self.db)}}):
            with self.assertRaises(SystemExit):
                backup_db.backup(self.backups, prefix="db-manual-")
        self.assertEqual(list(self.backups.glob("db-manual-*")), [])


class RotationTests(SimpleTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)

    def make(self, stamp, version, prefix="db-before-update-"):
        path = self.folder / f"{prefix}2026{stamp:010d}.sqlite3"
        with closing(sqlite3.connect(path)) as con:
            con.execute("CREATE TABLE django_migrations (id INTEGER PRIMARY KEY, app TEXT, name TEXT)")
            for n in range(version):
                con.execute("INSERT INTO django_migrations (app, name) VALUES ('bluesky_monitor', ?)", (f"{n:04d}",))
            con.commit()
        return path

    def names(self, prefix="db-before-update-"):
        return sorted(p.name for p in self.folder.glob(f"{prefix}*.sqlite3"))

    def test_the_newest_five_of_one_version_are_kept(self):
        made = [self.make(i, 9) for i in range(8)]
        backup_db.rotate(self.folder, "db-before-update-")
        self.assertEqual(self.names(), sorted(p.name for p in made[-5:]))

    def test_the_backup_from_before_a_database_update_survives_later_updates(self):
        older = [self.make(i, 8) for i in range(3)]  # before the update that changed the database
        for i in range(10, 22):  # many later updates (most with nothing new)
            self.make(i, 9)
        backup_db.rotate(self.folder, "db-before-update-")
        names = self.names()
        self.assertEqual(len(names), 6)
        self.assertIn(older[-1].name, names, "the newest backup of the earlier version is kept")
        self.assertNotIn(older[0].name, names)

    def test_one_backup_per_earlier_version_and_a_limit(self):
        for v in range(1, 10):
            self.make(v, v)
        for i in range(20, 26):
            self.make(i, 10)
        backup_db.rotate(self.folder, "db-before-update-", keep=5, keep_earlier=3)
        self.assertEqual(len(self.names()), 8)

    def test_other_kinds_are_left_alone_and_unreadable_files_arent_protected(self):
        manual = self.make(1, 1, prefix="db-manual-")
        (self.folder / "db-before-update-20260000000000.sqlite3").write_bytes(b"not a database")
        for i in range(10, 16):
            self.make(i, 9)
        backup_db.rotate(self.folder, "db-before-update-")
        self.assertTrue(manual.exists())
        self.assertEqual(len(self.names()), 5)


class RestoreReportTests(DataFolderTestCase):
    """manage.py restore_db --report: what a restore would undo. Changes nothing."""

    def setUp(self):
        super().setUp()
        self.manage("prepare_data")
        self.shell(f"""
from bluesky_monitor.models import MonitoredAccount
from discord_alerts.models import DiscordWebhook
from botconfig import store
DiscordWebhook.objects.create(name='#news', url='{FAKE_HOOK}', is_default=True)
MonitoredAccount.objects.create(handle='kept.bsky.social', first_poll_done=True)
store.save({{'bluesky_handle': 'bot.bsky.social', 'bluesky_password': 'fake-secret-pass', 'poll_interval_minutes': 5}})
""")
        self.manage("backup_db", "--label", "manual")
        [self.backup] = self.backups.glob("db-manual-*.sqlite3")

    def report(self, backup=None):
        res = self.manage("restore_db", "--report", str(backup or self.backup), ok=False)
        self.assertNotIn("fake-report-token", res.stdout + res.stderr, "webhook links are never shown")
        self.assertNotIn("fake-secret-pass", res.stdout + res.stderr, "passwords are never shown")
        return res.returncode, res.stdout + res.stderr

    def test_nothing_changed_since_the_backup(self):
        code, out = self.report()
        self.assertEqual(code, 0, out)
        self.assertIn("Nothing you set up and no notification history would be lost", out)

    def test_bookkeeping_and_activity_alone_are_not_a_loss(self):
        self.shell("""
from django.utils import timezone
from activity.models import ActivityEvent
from bluesky_monitor.models import MonitoredAccount
MonitoredAccount.objects.update(last_checked_at=timezone.now(), last_check_ok=True)
ActivityEvent.objects.create(type='check_ok', level='info', message='Checked.')
""")
        code, out = self.report()
        self.assertEqual(code, 0, out)
        self.assertIn("1 entry in the activity list since the backup would be removed", out)

    def test_a_new_account_and_channel_are_named(self):
        self.shell("""
from bluesky_monitor.models import MonitoredAccount
from discord_alerts.models import DiscordWebhook
MonitoredAccount.objects.create(handle='new.bsky.social')
DiscordWebhook.objects.create(name='#second', url='https://discord.com/api/webhooks/2/fake-report-token')
""")
        code, out = self.report()
        self.assertEqual(code, datastate_exit(), out)
        self.assertIn("1 account added since the backup would be removed: new.bsky.social", out)
        self.assertIn("1 Discord channel added since the backup would be removed: #second", out)

    def test_deleted_and_changed_setup_is_reported(self):
        self.shell("""
from bluesky_monitor.models import MonitoredAccount
from discord_alerts.models import DiscordWebhook
from botconfig import store
MonitoredAccount.objects.filter(handle='kept.bsky.social').update(is_active=False)
DiscordWebhook.objects.all().delete()
store.save({'poll_interval_minutes': 15})
""")
        code, out = self.report()
        self.assertEqual(code, datastate_exit(), out)
        self.assertIn("1 account changed since the backup would go back to how they were: kept.bsky.social", out)
        self.assertIn("1 Discord channel deleted since the backup would come back: #news", out)
        self.assertIn("The bot's settings (Bluesky login or check frequency) changed", out)

    def test_new_posts_and_sent_notifications_warn_about_sending_twice(self):
        self.shell("""
from types import SimpleNamespace
from bluesky_monitor import poller
from bluesky_monitor.models import MonitoredAccount, PostDelivery, SeenPost
account = MonitoredAccount.objects.get(handle='kept.bsky.social')
for n in range(3):
    item = SimpleNamespace(post=SimpleNamespace(uri=f'at://did:plc:k/app.bsky.feed.post/{n}', cid='c',
        record=SimpleNamespace(text='t'), author=SimpleNamespace(handle='kept.bsky.social')))
    poller.claim_post(account, item, SeenPost.PENDING)
PostDelivery.objects.filter(pk__in=list(PostDelivery.objects.values_list('pk', flat=True)[:2])).update(status='sent')
""")
        code, out = self.report()
        self.assertEqual(code, datastate_exit(), out)
        self.assertIn("3 posts the bot noticed since the backup would be forgotten", out)
        self.assertIn("2 notifications were sent to Discord since the backup", out)
        self.assertIn("can appear twice in Discord", out)

    def test_a_backup_of_an_earlier_database_version_is_compared_like_with_like(self):
        self.manage("migrate", "bluesky_monitor", "0007")
        self.manage("backup_db", "--label", "before-update")
        [older] = self.backups.glob("db-before-update-*.sqlite3")
        self.manage("prepare_data")  # the update to this version
        code, out = self.report(older)
        self.assertEqual(code, 0, out)

    def test_a_backup_from_a_newer_version_is_refused(self):
        newer = self.tmp / "db-manual-newer.sqlite3"
        shutil.copyfile(self.backup, newer)
        with closing(sqlite3.connect(newer)) as con:
            con.execute("INSERT INTO django_migrations (app, name, applied) VALUES "
                        "('bluesky_monitor', '9999_from_the_future', '2027-01-01')")
            con.commit()
        code, out = self.report(newer)
        self.assertEqual(code, 3, out)
        self.assertIn("was made by a newer version of the bot", out)
        res = self.manage("restore_db", str(newer), ok=False)
        self.assertEqual(res.returncode, 3, "putting it back is refused too")

    def test_a_damaged_backup_is_refused(self):
        bad = self.tmp / "db-manual-bad.sqlite3"
        bad.write_bytes(b"not a database" * 200)
        code, out = self.report(bad)
        self.assertEqual(code, 1, out)
        self.assertIn("is damaged", out)


def datastate_exit():
    from botconfig.management.commands.restore_db import LOSES_DATA_EXIT

    return LOSES_DATA_EXIT


class CompareTests(SimpleTestCase):
    """datastate.compare and describe on hand-made snapshots."""

    def snap(self, **kw):
        base = {"labels": {}, "accounts": {}, "channels": {}, "destinations": {}, "settings": {"1": "s"},
                "posts": {}, "deliveries": {}, "sending": 0, "activity": []}
        return {**base, **kw}

    def test_same_data_loses_nothing(self):
        impact = datastate.compare(self.snap(), self.snap())
        self.assertFalse(impact.loses_data)
        self.assertEqual(datastate.describe(impact), [])

    def test_posts_removed_since_the_backup_by_tidying_are_not_a_loss(self):
        impact = datastate.compare(self.snap(), self.snap(posts={"1|at://x": "sent"}))
        self.assertFalse(impact.loses_data, "a restore brings them back; nothing is lost")

    def test_retries_in_progress_are_not_a_loss_but_finished_ones_are(self):
        now = self.snap(deliveries={"k1": "retry", "k2": "failed"})
        then = self.snap(deliveries={"k1": "pending", "k2": "pending"})
        impact = datastate.compare(now, then)
        self.assertEqual(impact.finished, {"failed": 1})
        self.assertTrue(impact.loses_data)
