"""Rollback: going back to the code before step 3 must keep working.

1. The botconfig migration can be undone and redone without touching other data.
2. The real step 2 code (taken from Git) runs against a database and .env produced by
   the new code, and sees the settings saved in the control panel.

Test 2 needs Git and the step 2 commit; it is skipped if either is missing.
"""
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TransactionTestCase

from bluesky_monitor.models import MonitoredAccount

STEP2_COMMIT = "1a6689f"
ROOT = Path(settings.BASE_DIR)
LEGACY_KEYS = ("BLUESKY_HANDLE", "BLUESKY_PASSWORD", "DISCORD_WEBHOOK_URL", "POLL_INTERVAL_MINUTES")


class MigrationRollbackTests(TransactionTestCase):
    def test_undo_and_redo_the_settings_migration_keeps_other_data(self):
        from . import store
        MonitoredAccount.objects.create(handle="keep.bsky.social")
        store.save({"poll_interval_minutes": 9})

        executor = MigrationExecutor(connection)
        executor.migrate([("botconfig", None)])  # undo: drops the settings table only
        self.assertNotIn("botconfig_botsettings", connection.introspection.table_names())
        self.assertTrue(MonitoredAccount.objects.filter(handle="keep.bsky.social").exists())

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())  # redo
        self.assertEqual(store.get().version, 1, "a fresh import after a redo")
        self.assertTrue(MonitoredAccount.objects.filter(handle="keep.bsky.social").exists())


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=60)


class OldCodeRollbackTests(SimpleTestCase):
    """Runs real processes: the new code saves settings, then the old code reads them."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if shutil.which("git") is None or _git("cat-file", "-e", f"{STEP2_COMMIT}^{{commit}}").returncode:
            raise cls.skipTest(cls, "Git or the step 2 commit isn't available")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.old = self.dir / "step2"
        archive = _git("archive", "--format=tar", STEP2_COMMIT)
        self.assertEqual(archive.returncode, 0, archive.stderr)
        with tarfile.open(fileobj=BytesIO(archive.stdout)) as tar:
            tar.extractall(self.old)
        self.db = self.dir / "bot.sqlite3"
        self.env_file = self.old / ".env"  # where the old code's load_dotenv() finds it
        self.env = {k: v for k, v in os.environ.items() if k not in LEGACY_KEYS and k != "RUN_MAIN"}
        self.env.update(DATABASE_PATH=str(self.db), SCHEDULER_IN_WEB="0", BOT_LOG_LEVEL="CRITICAL",
                        SETTINGS_ENV_MIRROR_PATH=str(self.env_file), PYTHONIOENCODING="utf-8")

    def run_manage(self, root, *args, env=None):
        res = subprocess.run([sys.executable, str(root / "manage.py"), *args], cwd=root, env=env or self.env,
                             capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return res.stdout

    def test_old_code_uses_the_settings_saved_by_the_new_code(self):
        self.env_file.write_text("SECRET_KEY=old-key\nBLUESKY_HANDLE=before.bsky.social\n"
                                 "BLUESKY_PASSWORD=old-pw\nPOLL_INTERVAL_MINUTES=5\n", encoding="utf-8")
        new_env = dict(self.env)
        for line in self.env_file.read_text(encoding="utf-8").splitlines():  # as load_dotenv would
            key, _, value = line.partition("=")
            new_env[key] = value

        # New code: migrate, import from .env, then save new settings like the control panel does.
        self.run_manage(ROOT, "migrate", "--fake-initial", env=new_env)
        self.run_manage(ROOT, "shell", "-c", (
            "from botconfig import store; store.startup_check(); "
            "r = store.save({'bluesky_handle': 'after.bsky.social', 'bluesky_password': 'new pw#1', "
            "'discord_webhook_url': 'https://discord.com/api/webhooks/1/tok', 'poll_interval_minutes': 7}); "
            "assert r.env_file_ok"), env=new_env)

        # Old code: its migrate must accept the newer database, and its settings must be the saved ones.
        self.run_manage(self.old, "migrate", "--fake-initial")
        out = self.run_manage(self.old, "shell", "-c", (
            "from django.conf import settings as s; "
            "print(s.BLUESKY_HANDLE, s.BLUESKY_PASSWORD == 'new pw#1', s.DISCORD_WEBHOOK_URL.endswith('/1/tok'), "
            "s.POLL_INTERVAL_MINUTES, sep='|')"))
        self.assertEqual(out.strip(), "after.bsky.social|True|True|7")
        self.assertIn("SECRET_KEY=old-key", self.env_file.read_text(encoding="utf-8"))
