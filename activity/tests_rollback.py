"""Rollback from step 5 to step 4: the real step 4 code (from Git) runs on a database that
the step 5 code has used (activity log, check health, check summaries), then step 5 runs
again on what step 4 left behind.

Skipped if Git or the step 4 commit isn't available.
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
from django.test import SimpleTestCase

STEP4_COMMIT = "c5b9d1a"
ROOT = Path(settings.BASE_DIR)
LEGACY_KEYS = ("BLUESKY_HANDLE", "BLUESKY_PASSWORD", "DISCORD_WEBHOOK_URL", "POLL_INTERVAL_MINUTES")
HOOK = "https://discord.com/api/webhooks/1/general-token-aaaaaaaaaaaaaaaa"

USE_WITH_STEP5 = f"""
from botconfig import store
from discord_alerts import channels
from discord_alerts.models import DiscordWebhook
from bluesky_monitor import locks, scheduler
from bluesky_monitor.models import MonitoredAccount
from activity import log
store.startup_check()
general = DiscordWebhook.objects.create(name='#general', url='{HOOK}')
channels.make_default(general)
a = MonitoredAccount.objects.create(handle='logged.bsky.social', is_active=False)
MonitoredAccount.objects.filter(pk=a.pk).update(last_check_ok=False, last_check_category='account_not_found')
log.record('post_sent', 'success', 'sent', account=a, channel=general)
scheduler.heartbeat('step5')
print('cycle', scheduler.run_cycle('step5'))
locks.release_scheduler_lease('step5')
"""

USE_WITH_STEP4 = """
from bluesky_monitor import locks, scheduler
from bluesky_monitor.models import MonitoredAccount
from discord_alerts.models import DiscordWebhook
print('cycle', scheduler.run_cycle('step4'))
locks.release_scheduler_lease('step4')
MonitoredAccount.objects.get(handle='logged.bsky.social').delete()   # it has activity entries
MonitoredAccount.objects.create(handle='added.bsky.social')
DiscordWebhook.objects.create(name='#new', url='https://discord.com/api/webhooks/9/z').delete()
print('changes ok')
"""

BACK_TO_STEP5 = """
from activity import status
from activity.models import ActivityEvent
from bluesky_monitor.models import MonitoredAccount
data = status.compute()
print('status', data['scheduler']['state'], data['scheduler']['last_check']['result'])
print('kept', ActivityEvent.objects.filter(type='post_sent').values_list('account_label', flat=True).first())
print('added', MonitoredAccount.objects.get(handle='added.bsky.social').last_check_ok)
"""


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=60)


class Step4RollbackTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if shutil.which("git") is None or _git("cat-file", "-e", f"{STEP4_COMMIT}^{{commit}}").returncode:
            raise cls.skipTest(cls, "Git or the step 4 commit isn't available")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.old = self.dir / "step4"
        archive = _git("archive", "--format=tar", STEP4_COMMIT)
        self.assertEqual(archive.returncode, 0, archive.stderr)
        with tarfile.open(fileobj=BytesIO(archive.stdout)) as tar:
            tar.extractall(self.old)
        self.env = {k: v for k, v in os.environ.items() if k not in LEGACY_KEYS and k != "RUN_MAIN"}
        self.env.update(DATABASE_PATH=str(self.dir / "bot.sqlite3"), SCHEDULER_IN_WEB="0", BOT_LOG_LEVEL="CRITICAL",
                        SETTINGS_ENV_MIRROR_PATH=str(self.dir / ".env"), PYTHONIOENCODING="utf-8")

    def run_manage(self, root, *args):
        res = subprocess.run([sys.executable, str(root / "manage.py"), *args], cwd=root, env=self.env,
                             capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return res.stdout

    def test_step4_code_runs_on_a_step5_database_and_back(self):
        self.run_manage(ROOT, "migrate")
        self.assertIn("cycle True", self.run_manage(ROOT, "shell", "-c", USE_WITH_STEP5))
        self.run_manage(self.old, "migrate", "--fake-initial")
        out = self.run_manage(self.old, "shell", "-c", USE_WITH_STEP4)
        self.assertIn("cycle True", out)
        self.assertIn("changes ok", out)
        self.run_manage(ROOT, "migrate")
        out = self.run_manage(ROOT, "shell", "-c", BACK_TO_STEP5).splitlines()
        self.assertIn("kept @logged.bsky.social", out, "the log keeps entries of an account deleted by old code")
        self.assertIn("added None", out, "accounts added by old code have no check result yet")
        self.assertTrue(any(line.startswith("status ") for line in out))
