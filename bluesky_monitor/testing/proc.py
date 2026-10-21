"""Helper for the cross-process tests (bluesky_monitor/tests_processes.py).

Runs one action in its own Python process against a shared SQLite file:

    python bluesky_monitor/testing/proc.py <db file> <action> [args...]

Bluesky and Discord are faked. Discord "sends" are appended to a log file, so the
test can count exactly how many times each post was delivered.
"""
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
os.environ["SCHEDULER_IN_WEB"] = "0"
os.environ["SETTINGS_ENV_MIRROR_PATH"] = ""  # never touch the real .env

db_file, action, *args = sys.argv[1:]

from django.conf import settings  # noqa: E402

settings.DATABASES["default"]["NAME"] = db_file
import django  # noqa: E402

django.setup()

from django.core.management import call_command  # noqa: E402

from bluesky_monitor import locks, poller, scheduler  # noqa: E402
from bluesky_monitor.models import MonitoredAccount, SeenPost  # noqa: E402
from discord_alerts import sender  # noqa: E402

N_POSTS = 5


def item(n):
    return SimpleNamespace(post=SimpleNamespace(
        uri=f"at://did:plc:t/app.bsky.feed.post/{n}", cid=f"c{n}",
        record=SimpleNamespace(text=f"post {n}"), author=SimpleNamespace(handle="artist.bsky.social")))


class FakeSession:
    def author_feed(self, actor, cursor=None, limit=30):
        return SimpleNamespace(feed=[item(n) for n in range(N_POSTS, 0, -1)], cursor=None)


def fake_sender(log_path, crash=False):
    def deliver(webhook_url, handle, display_name, text, post_url):
        if crash:
            os._exit(3)  # killed after claiming, before Discord answered
        time.sleep(0.15)  # a slow Discord widens any race window
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(post_url + "\n")
        return sender.DeliveryResult(True)
    return deliver


def wait_until(ts):
    while time.time() < float(ts):
        time.sleep(0.005)


def install_fakes(log_path, crash=False):
    poller._new_session = lambda: FakeSession()
    sender.deliver_alert = fake_sender(log_path, crash)


if action == "migrate":
    call_command("migrate", verbosity=0)
    MonitoredAccount.objects.create(handle="artist.bsky.social", first_poll_done=True)

elif action == "lease":  # lease <instance> <hold seconds> <release|crash>
    instance, hold, mode = args
    got = locks.acquire_scheduler_lease(instance)
    print("ACQUIRED" if got else "DENIED", flush=True)
    end = time.time() + float(hold)
    while got and time.time() < end:
        time.sleep(0.2)
        locks.acquire_scheduler_lease(instance)  # heartbeat
    if got and mode == "release":
        locks.release_scheduler_lease(instance)
    if mode == "crash":
        os._exit(0)  # no release, like a killed process

elif action == "poll":  # poll <log> <start_ts>
    log, start = args
    install_fakes(log)
    account = MonitoredAccount.objects.get(handle="artist.bsky.social")
    wait_until(start)
    try:
        print("SENT", poller.poll_account(account), flush=True)
    except poller.AccountBusy:
        print("BUSY", flush=True)

elif action == "claim_and_deliver":  # skips the account lock, to test the claim layer alone
    log, start = args
    install_fakes(log)
    account = MonitoredAccount.objects.get(handle="artist.bsky.social")
    wait_until(start)
    for it in reversed(FakeSession().author_feed("x").feed):
        poller.claim_post(account, it, SeenPost.PENDING)
    total = 0
    while True:  # deliver_pending stops at the first failure; with no failures one pass is enough
        n = poller.deliver_pending(account)
        total += n
        if n == 0:
            break
    print("SENT", total, flush=True)

elif action == "crash_mid_send":
    log, = args
    install_fakes(log, crash=True)
    account = MonitoredAccount.objects.get(handle="artist.bsky.social")
    poller.poll_account(account)  # exits inside the first send

elif action == "deliver":
    log, = args
    install_fakes(log)
    account = MonitoredAccount.objects.get(handle="artist.bsky.social")
    print("SENT", poller.deliver_pending(account), flush=True)

elif action == "cycle":  # cycle <instance> <log> <start_ts>
    instance, log, start = args
    install_fakes(log)
    wait_until(start)
    print("RAN" if scheduler.run_cycle(instance) else "STANDBY", flush=True)

elif action == "status":
    for p in SeenPost.objects.order_by("id"):
        print(p.post_uri.rsplit("/", 1)[1], p.status, p.attempts)
