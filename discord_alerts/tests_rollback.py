"""Rollback from step 4 to step 3: the real step 3 code (from Git) runs on a database that the
step 4 code set up with named channels, and must send every account to the same place.

Skipped if Git or the step 3 commit isn't available.
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

STEP3_COMMIT = "439a66c"
ROOT = Path(settings.BASE_DIR)
LEGACY_KEYS = ("BLUESKY_HANDLE", "BLUESKY_PASSWORD", "DISCORD_WEBHOOK_URL", "POLL_INTERVAL_MINUTES")
GENERAL = "https://discord.com/api/webhooks/1/general-token-aaaaaaaaaaaaaaaa"
ART = "https://discord.com/api/webhooks/2/art-token-bbbbbbbbbbbbbbbbbbbb"
NEWS = "https://discord.com/api/webhooks/3/news-token-cccccccccccccccccc"

SETUP_WITH_STEP4 = f"""
from botconfig import store
from discord_alerts import channels
from discord_alerts.models import DiscordWebhook
from bluesky_monitor.models import MonitoredAccount
store.startup_check()
general = DiscordWebhook.objects.create(name='#general', url='{GENERAL}')
art = DiscordWebhook.objects.create(name='#art', url='{ART}')
news = DiscordWebhook.objects.create(name='#news', url='{NEWS}')
channels.make_default(general)
for handle, channel in [('follower', None), ('art', art), ('pinned', general), ('moved', art)]:
    channels.assign(MonitoredAccount.objects.create(handle=handle + '.bsky.social'), channel)
channels.make_default(news)                                   # followers move; explicit accounts stay
channels.assign(MonitoredAccount.objects.get(handle='moved.bsky.social'), None)
"""

READ_WITH_STEP3 = """
from bluesky_monitor import poller
from bluesky_monitor.models import MonitoredAccount
from discord_alerts.models import DiscordWebhook
for a in MonitoredAccount.objects.order_by('handle'):
    print(a.handle, poller._webhook_for(a).split('/')[-2], sep='=')
# Old code can still add channels and accounts on the newer database.
DiscordWebhook.objects.create(name='#added-after-rollback', url='https://discord.com/api/webhooks/9/z')
MonitoredAccount.objects.create(handle='added.bsky.social')
print('inserts ok')
"""


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=60)


class Step3RollbackTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if shutil.which("git") is None or _git("cat-file", "-e", f"{STEP3_COMMIT}^{{commit}}").returncode:
            raise cls.skipTest(cls, "Git or the step 3 commit isn't available")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.old = self.dir / "step3"
        archive = _git("archive", "--format=tar", STEP3_COMMIT)
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

    def test_step3_code_routes_like_the_channels(self):
        self.run_manage(ROOT, "migrate")
        self.run_manage(ROOT, "shell", "-c", SETUP_WITH_STEP4)
        self.run_manage(self.old, "migrate", "--fake-initial")
        out = self.run_manage(self.old, "shell", "-c", READ_WITH_STEP3).split()
        # webhook ids: 1 = #general, 2 = #art, 3 = #news (the current default)
        self.assertEqual(out[:4], ["art.bsky.social=2", "follower.bsky.social=3",
                                   "moved.bsky.social=3", "pinned.bsky.social=1"])
        self.assertEqual(out[4:], ["inserts", "ok"])
