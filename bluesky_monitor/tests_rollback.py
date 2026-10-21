"""Rollback from step 6 to step 5: the real step 5 code (from Git) runs on a database that
the step 6 code has used (announce options, check history, post kinds), then step 6 runs
again on what step 5 left behind.

Skipped if Git or the step 5 commit isn't available.
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

STEP5_COMMIT = "a739a3d"
ROOT = Path(settings.BASE_DIR)
LEGACY_KEYS = ("BLUESKY_HANDLE", "BLUESKY_PASSWORD", "DISCORD_WEBHOOK_URL", "POLL_INTERVAL_MINUTES")

ITEM = """
from types import SimpleNamespace
def item(n, author='old.bsky.social'):
    return SimpleNamespace(post=SimpleNamespace(uri=f'at://did:plc:o/app.bsky.feed.post/{n}', cid=f'c{n}',
        record=SimpleNamespace(text='t'), author=SimpleNamespace(handle=author)))
"""

USE_WITH_STEP6 = ITEM + """
from bluesky_monitor import poller, scheduler, locks
from bluesky_monitor.models import MonitoredAccount, SeenPost
from botconfig import store
store.startup_check()
a = MonitoredAccount.objects.create(handle='step6.bsky.social', first_poll_done=True, announce_replies=True)
poller.claim_post(a, item(1), SeenPost.SENT)
MonitoredAccount.objects.filter(pk=a.pk).update(last_check_ok=False, last_check_category='account_unavailable')
scheduler.heartbeat('step6')
print('cycle', scheduler.run_cycle('step6'))
locks.release_scheduler_lease('step6')
"""

USE_WITH_STEP5 = ITEM + """
from bluesky_monitor import locks, poller, scheduler
from bluesky_monitor.models import MonitoredAccount, SeenPost
from activity import status
print('cycle', scheduler.run_cycle('step5'))
locks.release_scheduler_lease('step5')
old = MonitoredAccount.objects.create(handle='old.bsky.social', first_poll_done=True)
poller.claim_post(old, item(2), SeenPost.PENDING)
MonitoredAccount.objects.get(handle='step6.bsky.social').delete()
status.compute()
print('changes ok')
"""

BACK_TO_STEP6 = """
from activity import status
from bluesky_monitor.models import MonitoredAccount, SeenPost
from bluesky_monitor.serializers import MonitoredAccountSerializer
row = MonitoredAccountSerializer(MonitoredAccount.objects.get(handle='old.bsky.social')).data
print('announce', row['announce_posts'], row['announce_reposts'], row['announce_replies'])
print('state', row['state'])
print('kind', SeenPost.objects.get(post_uri__endswith='/2').kind)
print('status', status.compute()['state'])
"""


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=60)


class Step5RollbackTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if shutil.which("git") is None or _git("cat-file", "-e", f"{STEP5_COMMIT}^{{commit}}").returncode:
            raise cls.skipTest(cls, "Git or the step 5 commit isn't available")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.old = self.dir / "step5"
        archive = _git("archive", "--format=tar", STEP5_COMMIT)
        self.assertEqual(archive.returncode, 0, archive.stderr)
        with tarfile.open(fileobj=BytesIO(archive.stdout)) as tar:
            tar.extractall(self.old)
        self.env = {k: v for k, v in os.environ.items() if k not in LEGACY_KEYS and k != "RUN_MAIN"}
        # Blank, not missing: load_dotenv() never overrides a key that is set, so the real .env's
        # Bluesky login and webhook can't reach these processes (they would sign in for real).
        self.env.update({k: "" for k in LEGACY_KEYS})
        self.env.update(DATABASE_PATH=str(self.dir / "bot.sqlite3"), SCHEDULER_IN_WEB="0", BOT_LOG_LEVEL="CRITICAL",
                        SETTINGS_ENV_MIRROR_PATH=str(self.dir / ".env"), PYTHONIOENCODING="utf-8")

    def run_manage(self, root, *args):
        res = subprocess.run([sys.executable, str(root / "manage.py"), *args], cwd=root, env=self.env,
                             capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return res.stdout

    def test_step5_code_runs_on_a_step6_database_and_back(self):
        self.run_manage(ROOT, "migrate")
        self.assertIn("cycle True", self.run_manage(ROOT, "shell", "-c", USE_WITH_STEP6))
        out = self.run_manage(self.old, "shell", "-c", USE_WITH_STEP5)
        self.assertIn("cycle True", out)
        self.assertIn("changes ok", out)
        self.run_manage(ROOT, "migrate")
        out = self.run_manage(ROOT, "shell", "-c", BACK_TO_STEP6).splitlines()
        self.assertIn("announce True True False", out, "accounts added by old code get the defaults")
        self.assertIn("state active", out)
        self.assertIn("kind None", out, "posts noted by old code have no kind and are sent as plain posts")
        self.assertTrue(any(line.startswith("status ") for line in out))


# -- step 7 -> step 6 -----------------------------------------------------------------------------
STEP6_COMMIT = "9d849eb"

STEP7_SCRIPT = """
from bluesky_monitor import jobs, locks
from bluesky_monitor.models import CheckJob, MonitoredAccount
from botconfig import store
store.startup_check()
MonitoredAccount.objects.create(handle='step7.bsky.social', first_poll_done=True)
job, created = jobs.request_check_all()
locks.acquire_scheduler_lease('step7')
print('ran', jobs.run_pending('step7'))
locks.release_scheduler_lease('step7')
print('job', CheckJob.objects.get(pk=job.pk).status)
"""

OLD_STEP6_SCRIPT = """
from bluesky_monitor import check_all, locks, scheduler
from bluesky_monitor.models import MonitoredAccount
from activity import status
print('cycle', scheduler.run_cycle('step6'))
locks.release_scheduler_lease('step6')
MonitoredAccount.objects.create(handle='old6.bsky.social', first_poll_done=True)
print('check_all', check_all.run()['result'])
status.compute()
print('changes ok')
"""

BACK_TO_STEP7_SCRIPT = """
from bluesky_monitor import jobs
from bluesky_monitor.models import CheckJob, MonitoredAccount
print('old job', jobs.describe(CheckJob.objects.get())['status'])
job, created = jobs.request_check_all()
print('new job', created)
print('accounts', MonitoredAccount.objects.count())
"""


class Step6RollbackTests(Step5RollbackTests):
    """The real step 6 code (with the admin still in it) on a database the step 7 code has used."""

    commit = STEP6_COMMIT

    @classmethod
    def setUpClass(cls):
        SimpleTestCase.setUpClass.__func__(cls)
        if shutil.which("git") is None or _git("cat-file", "-e", f"{cls.commit}^{{commit}}").returncode:
            raise cls.skipTest(cls, "Git or the step 6 commit isn't available")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.old = self.dir / "step6"
        archive = _git("archive", "--format=tar", self.commit)
        self.assertEqual(archive.returncode, 0, archive.stderr)
        with tarfile.open(fileobj=BytesIO(archive.stdout)) as tar:
            tar.extractall(self.old)
        self.env = {k: v for k, v in os.environ.items() if k not in LEGACY_KEYS and k != "RUN_MAIN"}
        # Blank, not missing: load_dotenv() never overrides a key that is set, so the real .env's
        # Bluesky login and webhook can't reach these processes (they would sign in for real).
        self.env.update({k: "" for k in LEGACY_KEYS})
        self.env.update(DATABASE_PATH=str(self.dir / "bot.sqlite3"), SCHEDULER_IN_WEB="0", BOT_LOG_LEVEL="CRITICAL",
                        SETTINGS_ENV_MIRROR_PATH=str(self.dir / ".env"), PYTHONIOENCODING="utf-8")

    def test_step5_code_runs_on_a_step6_database_and_back(self):
        self.skipTest("covered by Step5RollbackTests")

    def test_step6_code_runs_on_a_step7_database_and_back(self):
        self.run_manage(ROOT, "migrate")
        out = self.run_manage(ROOT, "shell", "-c", STEP7_SCRIPT)
        self.assertIn("ran 1", out)
        self.assertIn("job finished", out)
        self.run_manage(self.old, "check")
        self.run_manage(self.old, "migrate")  # the step 6 code adds back its admin tables; nothing else changes
        out = self.run_manage(self.old, "shell", "-c", OLD_STEP6_SCRIPT)
        self.assertIn("cycle True", out)
        self.assertIn("check_all not_configured", out, "no Bluesky login reaches the test processes")
        self.assertIn("changes ok", out)
        self.run_manage(ROOT, "migrate")
        out = self.run_manage(ROOT, "shell", "-c", BACK_TO_STEP7_SCRIPT).splitlines()
        self.assertIn("old job finished", out)
        self.assertIn("new job True", out)
        self.assertIn("accounts 2", out)


class RealEnvIsolationTests(Step6RollbackTests):
    """The processes these tests start must never see the owner's real Bluesky login or webhook."""

    def test_step6_code_runs_on_a_step7_database_and_back(self):
        self.skipTest("covered by Step6RollbackTests")

    def test_processes_get_no_real_credentials(self):
        self.run_manage(ROOT, "migrate")
        out = self.run_manage(ROOT, "shell", "-c", (
            "from botconfig import store; store.startup_check(); h, p = store.credentials(); "
            "print('credentials', bool(h), bool(p), bool(store.default_webhook_url()))"))
        self.assertIn("credentials False False False", out)
