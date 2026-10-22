"""What the Docker services run on start (prepare_data, wait_for_database,
checker_health, /api/health/, backup_db --label/--export, the checker watchdog).

The command tests run `manage.py` in separate processes on a temporary data folder, like
the containers do. The real .env's login is blanked out (load_dotenv never overrides a
key that is set), so no real credential reaches them.
"""
import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from bluesky_monitor import scheduler
from bluesky_monitor.models import SchedulerState
from .checks import private_paths

ROOT = Path(settings.BASE_DIR)
BLANKED = ("BLUESKY_HANDLE", "BLUESKY_PASSWORD", "DISCORD_WEBHOOK_URL", "POLL_INTERVAL_MINUTES", "SECRET_KEY",
           "LEGACY_ENV_FILE", "DATABASE_PATH", "BACKUP_DIR", "SECRET_KEY_FILE", "CACHE_DIR")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class DataFolderTestCase(SimpleTestCase):
    """A data folder laid out like the Docker volume: /data/db.sqlite3, /data/backups, /data/secret_key."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.data = self.tmp / "data"
        self.db, self.backups, self.key = self.data / "db.sqlite3", self.data / "backups", self.data / "secret_key"
        self.env = {k: v for k, v in os.environ.items() if k != "RUN_MAIN"}
        self.env.update({k: "" for k in BLANKED})
        self.env.update(DATABASE_PATH=str(self.db), BACKUP_DIR=str(self.backups), SECRET_KEY_FILE=str(self.key),
                        CACHE_DIR=str(self.data / "cache"), SCHEDULER_IN_WEB="0",
                        BOT_LOG_LEVEL="CRITICAL", PYTHONIOENCODING="utf-8")

    def manage(self, *args, ok=True, env=None):
        res = subprocess.run([sys.executable, str(ROOT / "manage.py"), *args], cwd=ROOT, env={**self.env, **(env or {})},
                             capture_output=True, text=True, encoding="utf-8", timeout=180)
        if ok:
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return res

    def shell(self, code, **kw):
        return self.manage("shell", "-c", code, **kw).stdout

    def auto_backups(self):
        return sorted(self.backups.glob("db-auto-*.sqlite3")) if self.backups.exists() else []


class PrepareDataTests(DataFolderTestCase):
    def test_first_start_creates_the_key_and_database_and_later_starts_change_nothing(self):
        out = self.manage("prepare_data").stdout
        self.assertIn("Created this install's secret key.", out)
        self.assertIn("Ready: 0 account(s), 0 channel(s), Bluesky login not set up yet", out)
        key = self.key.read_text(encoding="utf-8")
        self.assertGreaterEqual(len(key), 50)
        self.assertEqual(self.auto_backups(), [], "nothing to back up on a brand-new install")

        out = self.manage("prepare_data").stdout
        self.assertNotIn("Created this install's secret key", out)
        self.assertIn("The database is up to date.", out)
        self.assertEqual(self.key.read_text(encoding="utf-8"), key, "the key stays the same")
        self.assertIn(key[:12], self.shell("from django.conf import settings; print(settings.SECRET_KEY[:12])"))
        self.assertEqual(self.auto_backups(), [])

    def test_an_update_with_new_migrations_is_backed_up_first_and_keeps_the_data(self):
        self.manage("prepare_data")
        self.shell("from bluesky_monitor.models import MonitoredAccount; "
                   "MonitoredAccount.objects.create(handle='kept.bsky.social')")
        self.manage("migrate", "bluesky_monitor", "0005")  # the database as an older version left it
        out = self.manage("prepare_data").stdout
        self.assertIn("Database backed up to", out)
        [backup] = self.auto_backups()
        with closing(sqlite3.connect(backup)) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("bluesky_monitor_checkjob", tables, "the backup was taken before the update")
        self.assertIn("Ready: 1 account(s)", out)
        self.assertIn("kept.bsky.social", self.shell(
            "from bluesky_monitor.models import MonitoredAccount; print(list(MonitoredAccount.objects.values_list('handle', flat=True)))"))

    def test_if_the_backup_fails_the_database_is_not_changed(self):
        self.manage("prepare_data")
        self.manage("migrate", "bluesky_monitor", "0005")
        before = sha(self.db)
        with mock.patch.dict(os.environ, {}):
            res = self.manage("prepare_data", ok=False, env={"BACKUP_DIR": str(self.key)})  # a file, not a folder
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(sha(self.db), before, "no migration ran without a backup")
        self.assertIn("unapplied", self.manage("showmigrations", "bluesky_monitor", "--plan").stdout.replace(
            "[ ]", "unapplied"))

    def test_importing_an_earlier_install(self):
        old = self.tmp / "old install"
        old.mkdir()
        old_env = {"DATABASE_PATH": str(old / "db.sqlite3")}
        self.manage("migrate", env=old_env)
        self.shell("from bluesky_monitor.models import MonitoredAccount; "
                   "MonitoredAccount.objects.create(handle='old.bsky.social')", env=old_env)
        (old / ".env").write_text("SECRET_KEY=old-key\nBLUESKY_HANDLE=fake.bsky.social\nBLUESKY_PASSWORD=fake-pass-word\n"
                                  "DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/1/fake-token\n"
                                  "POLL_INTERVAL_MINUTES=7\n", encoding="utf-8")
        old_hashes = (sha(old / "db.sqlite3"), sha(old / ".env"))

        env = {"LEGACY_ENV_FILE": str(old / ".env")}
        out = self.manage("prepare_data", "--import-from", str(old), env=env).stdout
        self.assertIn("Copied the accounts, channels and history from the earlier version.", out)
        self.assertIn("Ready: 1 account(s), 1 channel(s), Bluesky login saved, checking every 7 minute(s).", out)
        self.assertNotIn("fake-pass-word", out)
        self.assertNotIn("fake-token", out)
        self.assertEqual((sha(old / "db.sqlite3"), sha(old / ".env")), old_hashes, "the old files are only read")
        self.assertNotEqual(self.key.read_text(encoding="utf-8").strip(), "old-key", "the old SECRET_KEY isn't used")

        out = self.manage("prepare_data", "--import-from", str(old), env=env).stdout
        self.assertIn("already has data, so nothing was imported", out)

    def test_a_damaged_old_database_is_not_imported(self):
        old = self.tmp / "old"
        old.mkdir()
        (old / "db.sqlite3").write_bytes(b"this is not a database" * 100)
        res = self.manage("prepare_data", "--import-from", str(old), ok=False)
        self.assertNotEqual(res.returncode, 0)
        self.assertFalse(self.db.exists() and self.db.stat().st_size > 0 and
                         b"not a database" in self.db.read_bytes())

    def test_backup_labels_and_export(self):
        self.manage("prepare_data")
        export = self.tmp / "windows backups folder"
        out = self.manage("backup_db", "--label", "before-update", "--export", str(export)).stdout
        [named] = list(self.backups.glob("db-before-update-*.sqlite3"))
        self.assertTrue((export / named.name).is_file())
        with closing(sqlite3.connect(export / named.name)) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertIn("Copy saved as", out)
        res = self.manage("backup_db", "--label", "../evil", ok=False)
        self.assertNotEqual(res.returncode, 0)

    def test_web_workers_share_one_cache(self):
        out = self.shell("from django.conf import settings; print(settings.CACHES['default']['BACKEND'])")
        self.assertIn("FileBasedCache", out)


class WaitForDatabaseTests(DataFolderTestCase):
    def test_the_checker_waits_for_the_update_and_then_starts(self):
        res = self.manage("wait_for_database", "--timeout", "1", "--interval", "0.2", ok=False)
        self.assertEqual(res.returncode, 1, "no database yet")
        self.assertIn("Docker will start the checker again", res.stderr)
        self.manage("prepare_data")
        self.assertIn("The database is ready.", self.manage("wait_for_database", "--timeout", "5").stdout)
        self.manage("migrate", "bluesky_monitor", "0005")
        res = self.manage("wait_for_database", "--timeout", "1", "--interval", "0.2", ok=False)
        self.assertEqual(res.returncode, 1, "an update is waiting")
        self.assertIn("waiting for the database update to finish", res.stdout)


class CheckerHealthTests(TestCase):
    def test_healthy_only_with_a_recent_heartbeat(self):
        with self.assertRaises(SystemExit):
            call_command("checker_health", stdout=open(os.devnull, "w"), stderr=open(os.devnull, "w"))
        SchedulerState.objects.create(pk=1, instance_id="c1", heartbeat_at=timezone.now())
        call_command("checker_health", stdout=open(os.devnull, "w"))
        SchedulerState.objects.filter(pk=1).update(heartbeat_at=timezone.now() - timedelta(minutes=5))
        with self.assertRaises(SystemExit):
            call_command("checker_health", stdout=open(os.devnull, "w"), stderr=open(os.devnull, "w"))


class HealthEndpointTests(TestCase):
    def test_ready(self):
        res = self.client.get("/api/health/")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual((body["status"], body["database"], body["migrations_pending"]), ("ok", True, 0))
        self.assertEqual(body["version"], settings.APP_VERSION)
        self.assertIn(body["checker"], ("not_started", "stopped", "running", "checking", "stalled"))
        self.assertEqual(res["Cache-Control"], "no-store")

    def test_not_ready_while_the_database_is_being_updated(self):
        with mock.patch("botconfig.management.commands.backup_db.pending_migrations", return_value=[1, 2]):
            res = self.client.get("/api/health/")
        self.assertEqual((res.status_code, res.json()["status"], res.json()["migrations_pending"]), (503, "updating", 2))

    def test_answers_to_the_container_healthcheck_host(self):
        self.assertEqual(self.client.get("/api/health/", HTTP_HOST="127.0.0.1:8000").status_code, 200)


class WatchdogTests(SimpleTestCase):
    def test_a_stalled_heartbeat_stops_the_checker_so_docker_restarts_it(self):
        exited = threading.Event()
        codes = []
        dog = scheduler.Watchdog(0.05, exit=lambda code: (codes.append(code), exited.set()), check_every=0.01)
        with mock.patch.object(scheduler, "_last_heartbeat", time.monotonic() - 1):
            dog.start()
            self.assertTrue(exited.wait(2))
        self.assertEqual(codes, [70])

    def test_a_working_heartbeat_keeps_it_running(self):
        dog = scheduler.Watchdog(60)
        scheduler._last_heartbeat = time.monotonic()
        self.assertFalse(dog.stalled())
        self.assertTrue(dog.stalled(now=time.monotonic() + 61))

    def test_heartbeat_resets_the_watchdog(self):
        scheduler._last_heartbeat = 0
        with mock.patch("bluesky_monitor.locks.acquire_scheduler_lease", return_value=False):
            scheduler.heartbeat("W")
        self.assertGreater(scheduler._last_heartbeat, time.monotonic() - 5)


class PrivateFilesTests(SimpleTestCase):
    def test_backups_and_the_key_file_count_as_private(self):
        with mock.patch.dict(os.environ, {"SECRET_KEY_FILE": "/data/secret_key", "LEGACY_ENV_FILE": "/import/.env"}):
            paths = private_paths()
        self.assertIn(Path("/data/secret_key").resolve(), paths)
        self.assertIn(Path("/import/.env").resolve(), paths)
        self.assertIn(Path(settings.BACKUP_DIR).resolve(), paths)


class CopyDatabaseTests(SimpleTestCase):
    def test_copies_data_still_in_the_wal_journal_without_writing_to_the_source(self):
        from .management.commands.prepare_data import copy_database

        with tempfile.TemporaryDirectory() as tmp:
            old, new = Path(tmp) / "old" / "db.sqlite3", Path(tmp) / "data" / "db.sqlite3"
            old.parent.mkdir()
            writer = sqlite3.connect(old)
            try:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.execute("PRAGMA wal_autocheckpoint=0")
                writer.execute("CREATE TABLE t (v TEXT)")
                writer.execute("INSERT INTO t VALUES ('only in the journal')")
                writer.commit()
                self.assertGreater(Path(f"{old}-wal").stat().st_size, 0)
                before = {p.name: sha(p) for p in old.parent.iterdir()}
                copy_database(old, new)
                self.assertEqual({p.name: sha(p) for p in old.parent.iterdir()}, before, "the source is only read")
            finally:
                writer.close()
            with closing(sqlite3.connect(new)) as conn:
                self.assertEqual(conn.execute("SELECT v FROM t").fetchall(), [("only in the journal",)])
            self.assertFalse((new.parent / "import-scratch").exists())
