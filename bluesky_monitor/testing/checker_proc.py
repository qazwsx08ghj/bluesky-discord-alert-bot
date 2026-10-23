"""A real checker process for the stop tests (bluesky_monitor/tests_shutdown.py).

    python bluesky_monitor/testing/checker_proc.py <db file> setup <accounts> <posts per account>
    python bluesky_monitor/testing/checker_proc.py <db file> run <log file> <seconds per send> [check_all]

setup: migrates a new database and adds accounts, one fake channel, and posts waiting to be
       sent to it (pending deliveries).
run:   runs scheduler.run_forever() as the Docker checker does, with Bluesky and Discord faked.
       A check of every account starts at once (or, with check_all, a queued Check All job).
       Each fake send writes "start <post>" to the log, takes <seconds per send>, then writes
       "done <post>". Nothing reaches the network.
"""
import os
import sys
import time
from datetime import datetime, timezone as dt_tz
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
os.environ["SCHEDULER_IN_WEB"] = "0"

db_file, action, *args = sys.argv[1:]
os.environ["DATABASE_PATH"] = db_file

import django  # noqa: E402

django.setup()

from django.core.management import call_command  # noqa: E402

from bluesky_monitor import poller, scheduler  # noqa: E402
from bluesky_monitor.models import CheckJob, MonitoredAccount, SeenPost  # noqa: E402
from discord_alerts import sender  # noqa: E402
from discord_alerts.models import DiscordWebhook  # noqa: E402

HOOK = "https://discord.com/api/webhooks/1/fake-test-token"


def item(account, n):
    return SimpleNamespace(post=SimpleNamespace(
        uri=f"at://did:plc:{account}/app.bsky.feed.post/{n}", cid=f"c{n}",
        record=SimpleNamespace(text=f"a{account} post {n}"), author=SimpleNamespace(handle=f"a{account}.bsky.social")))


def setup(accounts, posts):
    call_command("migrate", verbosity=0)
    DiscordWebhook.objects.create(name="#fake", url=HOOK, is_default=True)
    for a in range(int(accounts)):
        account = MonitoredAccount.objects.create(handle=f"a{a}.bsky.social", first_poll_done=True)
        for n in range(int(posts)):
            assert poller.claim_post(account, item(a, n), SeenPost.PENDING)


class NoNewPosts:
    def author_feed(self, actor, cursor=None, limit=30):
        return SimpleNamespace(feed=[], cursor=None)


def run(log_path, seconds, mode=""):
    def deliver(webhook_url, handle, display_name, text, post_url, **kwargs):
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"start {text}\n")
        time.sleep(float(seconds))
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"done {text}\n")
        return sender.DeliveryResult(True)

    poller._new_session = lambda: NoNewPosts()
    sender.deliver_alert = deliver
    if mode == "check_all":
        CheckJob.objects.create()
    else:
        original = scheduler._build

        def build(cls, instance_id):
            sched = original(cls, instance_id)
            sched.add_job(scheduler.run_cycle, kwargs={"instance_id": instance_id}, id="check_now",
                          next_run_time=datetime.now(tz=dt_tz.utc))
            return sched

        scheduler._build = build
    scheduler.run_forever()


if action == "setup":
    setup(*args)
elif action == "run":
    run(*args)
