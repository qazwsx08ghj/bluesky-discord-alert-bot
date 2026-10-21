"""A complete bot with a fake Bluesky and a fake Discord, for browser tests and screenshots.

    python config/testing/demo_server.py --port 8765 --scenario working

Everything runs in this one process: the real Django app, API and checker, against a
temporary database. Only the outside world is replaced:
- Bluesky: a small fake world of accounts and posts (profile lookup, login, feeds).
- Discord: sends are recorded in memory, never sent. A webhook URL containing "broken"
  is refused, as Discord does for a deleted webhook.
- The network: connections to anything but this computer are refused, so nothing can
  reach Bluesky or Discord even by mistake.

The real .env is never used: every setting it could provide is set here first
(python-dotenv doesn't override variables that already exist), and nothing writes .env.

Scenarios: `empty` (first visit), `working` (set up, with accounts and history) and
`problem` (the saved App Password is rejected).

Test-only addresses (they exist only in this process, never in the real app):
    GET /__demo__/reset/<scenario>/   start again with fresh data
    GET /__demo__/discord/            the messages "sent" to Discord so far
    GET /__demo__/post/<handle>/      that fake account writes a new post (?text=…)
"""
import argparse
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

GOOD_HANDLE = "alertbot-demo.bsky.social"
GOOD_PASSWORD = "demo-demo-demo-demo"  # any App Password-shaped value without "wrong" works
WEBHOOK = "https://discord.com/api/webhooks/1189000000000000001/demo-token-announcements"
WEBHOOK_2 = "https://discord.com/api/webhooks/1204000000000000002/demo-token-art-drops"


def _isolate(data_dir: Path):
    """Settings for this process only. Runs before Django is imported."""
    for key in ("SECRET_KEY_FILE", "CACHE_DIR", "DJANGO_SETTINGS_MODULE"):
        os.environ.pop(key, None)
    os.environ.update({
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "SECRET_KEY": "demo-server-not-a-real-secret-key-0123456789abcdef",
        "DEBUG": "False",
        "ALLOWED_HOSTS": "localhost,127.0.0.1,[::1]",
        # Blank values win over the real .env (load_dotenv never overrides).
        "BLUESKY_HANDLE": "", "BLUESKY_PASSWORD": "", "DISCORD_WEBHOOK_URL": "", "POLL_INTERVAL_MINUTES": "5",
        "LEGACY_ENV_FILE": str(data_dir / "no-legacy-env"),  # doesn't exist: nothing is imported
        "DATABASE_PATH": str(data_dir / "db.sqlite3"),
        "BACKUP_DIR": str(data_dir / "backups"),
        "SCHEDULER_IN_WEB": "0",  # started below, after the fakes are in place
        "CHECK_JOB_POLL_SECONDS": "0.5",
        "BOT_LOG_LEVEL": "WARNING",
        "ALERTBOT_VERSION": "demo",
        "CSRF_TRUSTED_ORIGINS": os.environ.get("DEMO_TRUSTED_ORIGINS", "http://localhost:5173"),
    })


def _block_network():
    """Refuse every connection that leaves this computer."""
    local = {"127.0.0.1", "::1", "localhost"}
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def check(address):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in local:
            raise OSError(f"Demo server: no network access (tried {host})")

    def connect(self, address):
        check(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        check(address)
        return real_connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):
        if host not in local and host is not None:
            raise socket.gaierror(f"Demo server: no network access (looked up {host})")
        return real_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, getaddrinfo


# -- the fake outside world -----------------------------------------------------------------------
PEOPLE = {
    "mira-draws.bsky.social": ("Mira Draws", [
        "Commissions open for March! Five slots, details in the thread.",
        "New sketchbook page: herons at the canal.",
    ]),
    "pixelforge.games": ("Pixel Forge Games", [
        "Patch 1.4 is live. New boss, new hats, fewer crashes on older GPUs.",
        "Dev diary #12: how we made the lighthouse level.",
    ]),
    "nightbus-news.bsky.social": ("Night Bus News", [
        "Route 12 night service returns Friday after the bridge works finish.",
    ]),
    "lowtide.band": ("Low Tide", ["Tour dates for the spring are up on our site."]),
    "tamsin.bsky.social": ("Tamsin Okoro", ["Sketchbook tour is up on my site, thank you all for 10k!"]),
    "greenhouse-weekly.bsky.social": ("Greenhouse Weekly", ["This week: tomatoes that survive a cold snap."]),
    "studio-kite.bsky.social": ("Studio Kite", ["Our short film premieres on Saturday."]),
}
FEEDS = {}       # handle -> list of post texts, newest first (grows when the demo "posts")
DISCORD = []     # what was "sent" to Discord
_lock = threading.Lock()


def _did(handle):
    return "did:plc:" + "".join(c for c in handle if c.isalnum())[:24]


def _item(handle, n, text):
    return SimpleNamespace(reason=None, post=SimpleNamespace(
        uri=f"at://{_did(handle)}/app.bsky.feed.post/demo{n}", cid=f"cid{n}",
        record=SimpleNamespace(text=text, reply=None), author=SimpleNamespace(handle=handle)))


def _resp(error, status=400):
    return SimpleNamespace(status_code=status, content=SimpleNamespace(error=error, message=error))


class FakeClient:
    """Stands in for atproto's Client for the bot's own login and for reading feeds."""

    def __init__(self, *args, **kwargs):
        self.callbacks, self.session, self.me = [], None, None

    def on_session_change(self, cb):
        self.callbacks.append(cb)

    def login(self, login=None, password=None, session_string=None, fetch_bsky_profile=True):
        from atproto_client import exceptions as at_exc
        from atproto_client.client.session import SessionEvent

        if session_string:
            self.session = session_string
            event = SessionEvent.IMPORT
        else:
            if not password or "wrong" in password or not login:
                raise at_exc.UnauthorizedError(_resp("AuthenticationRequired", 401))
            self.session = f"demo-session-{time.time()}"
            self.me = SimpleNamespace(did=_did(login), handle=login)
            self._session = self.me
            event = SessionEvent.CREATE
        for cb in self.callbacks:
            cb(event, None)

    def export_session_string(self):
        return self.session or ""

    def get_author_feed(self, actor, cursor=None, limit=30):
        from atproto_client import exceptions as at_exc

        from botconfig import store

        _, password = store.credentials()
        if "wrong" in (password or ""):
            raise at_exc.UnauthorizedError(_resp("ExpiredToken", 401))
        handle = actor.lstrip("@").lower()
        if handle not in PEOPLE:
            raise at_exc.BadRequestError(_resp("InvalidRequest"))
        texts = FEEDS.setdefault(handle, list(PEOPLE[handle][1]))
        n = len(texts)
        return SimpleNamespace(feed=[_item(handle, n - i, t) for i, t in enumerate(texts)], cursor=None)


class FakePublicClient:
    """Stands in for the public (no login) client used by the profile lookup."""

    def __init__(self, *args, **kwargs):
        pass

    def get_profile(self, actor):
        from atproto_client import exceptions as at_exc

        handle = actor.lstrip("@").lower()
        if "slow" in handle:
            time.sleep(1.5)
        if handle not in PEOPLE:
            raise at_exc.BadRequestError(_resp("InvalidRequest"))
        name, posts = PEOPLE[handle]
        return SimpleNamespace(handle=handle, did=_did(handle), display_name=name, avatar=None,
                               description=f"{name} on Bluesky (demo profile).", followers_count=1200,
                               posts_count=len(posts) * 40)


def fake_deliver(webhook_url, handle, display_name, text, post_url, kind="post", **kwargs):
    from discord_alerts.sender import DeliveryResult

    if "broken" in webhook_url:
        return DeliveryResult(False, "Discord didn't accept the webhook. It may have been deleted.", status_code=404,
                              category="discord_webhook_invalid", technical="HTTP 404: Unknown Webhook")
    with _lock:
        DISCORD.append({"channel_token": webhook_url.rsplit("/", 1)[-1], "handle": handle, "text": text})
    return DeliveryResult(True, status_code=200)


def install_fakes():
    from bluesky_monitor import bluesky, profiles
    from discord_alerts import sender

    bluesky.SharedSession.client_factory = FakeClient
    profiles.client_factory = FakePublicClient
    sender.deliver_alert = fake_deliver


# -- data -----------------------------------------------------------------------------------------
def seed(scenario: str):
    from django.utils import timezone

    from activity import log
    from activity.models import ActivityEvent
    from bluesky_monitor.models import BlueskySessionState, MonitoredAccount, SeenPost
    from botconfig import store
    from discord_alerts.models import DiscordWebhook

    FEEDS.clear()
    DISCORD.clear()
    store.get()
    if scenario == "empty":
        return
    now = timezone.now()
    password = GOOD_PASSWORD if scenario == "working" else "wrong-wrong-wrong-wrong"
    store.save({"bluesky_handle": GOOD_HANDLE, "bluesky_password": password, "poll_interval_minutes": 5})
    main = DiscordWebhook.objects.create(name="announcements", url=WEBHOOK, last_delivery_at=now, last_delivery_ok=True)
    from discord_alerts import channels
    channels.make_default(main)
    art = DiscordWebhook.objects.create(name="art-drops", url=WEBHOOK_2, last_delivery_at=now, last_delivery_ok=True)

    from bluesky_monitor.bluesky import _fingerprint
    BlueskySessionState.objects.update_or_create(pk=1, defaults=dict(
        handle=GOOD_HANDLE, credential_fingerprint=_fingerprint(GOOD_HANDLE, password),
        session_string="demo-session" if scenario == "working" else "",
        login_failures=0 if scenario == "working" else 1,
        login_retry_after=None if scenario == "working" else now + timedelta(minutes=10),
        last_error="" if scenario == "working" else "Bluesky didn't accept the handle or App Password."))

    rows = [  # handle, channel, active, first check done, minutes since checked
        ("mira-draws.bsky.social", art, True, True, 1),
        ("pixelforge.games", None, True, True, 2),
        ("nightbus-news.bsky.social", None, True, True, 2),
        ("lowtide.band", None, True, False, None),
        ("tamsin.bsky.social", art, False, True, 60 * 72),
    ]
    accounts = {}
    for i, (handle, channel, active, first_done, ago) in enumerate(rows):
        checked = now - timedelta(minutes=ago) if ago is not None else None
        acc = MonitoredAccount.objects.create(
            handle=handle, display_name=PEOPLE[handle][0], channel=channel, is_active=active,
            first_poll_done=first_done, last_checked_at=checked, last_check_ok=True if checked else None,
            last_success_at=checked, first_checked_at=now - timedelta(days=3) if first_done else None,
            first_check_posts=len(PEOPLE[handle][1]) if first_done else None)
        MonitoredAccount.objects.filter(pk=acc.pk).update(created_at=now - timedelta(days=5 - i))
        accounts[handle] = acc
        if first_done:  # the existing posts were noted on the first check
            FEEDS[handle] = list(PEOPLE[handle][1])
            for n, text in enumerate(reversed(PEOPLE[handle][1]), start=1):
                SeenPost.objects.create(account=acc, post_uri=f"at://{_did(handle)}/app.bsky.feed.post/demo{n}",
                                        post_cid=f"cid{n}", text=text, status=SeenPost.SENT,
                                        sent_at=now - timedelta(minutes=30 * n), kind="post",
                                        post_url=f"https://bsky.app/profile/{handle}/post/demo{n}")

    def event(minutes_ago, *args, **kwargs):
        e = log.record(*args, **kwargs)
        ActivityEvent.objects.filter(pk=e.pk).update(created_at=now - timedelta(minutes=minutes_ago),
                                                     last_seen_at=now - timedelta(minutes=minutes_ago))

    mira, pixel, night, low = (accounts[h] for h in ("mira-draws.bsky.social", "pixelforge.games",
                                                     "nightbus-news.bsky.social", "lowtide.band"))
    event(150, log.ACCOUNT_ADDED, "info", "Started watching @lowtide.band. The first check notes its existing "
          "posts without sending them.", account=low)
    event(62, log.POST_SENT, "success", "Sent a new post from @nightbus-news.bsky.social to \"announcements\".",
          account=night, channel=main, attempt=1, detail={"post_url": "https://bsky.app/profile/nightbus-news.bsky.social"
                                                          "/post/demo1", "post_text": PEOPLE[night.handle][1][0]})
    event(38, log.POST_SENT, "success", "Sent a new post from @pixelforge.games to \"announcements\".",
          account=pixel, channel=main, attempt=1, detail={"post_url": "https://bsky.app/profile/pixelforge.games/post/demo2",
                                                          "post_text": PEOPLE[pixel.handle][1][0]})
    event(14, log.SEND_FAILED, "warning", "Discord didn't accept a post from @pixelforge.games. It will be "
          "sent again in 2 minutes.", account=pixel, channel=main, attempt=1, category="discord_unavailable")
    event(12, log.POST_SENT, "success", "Sent a new post from @pixelforge.games to \"announcements\" (second try).",
          account=pixel, channel=main, attempt=2, detail={"post_url": "https://bsky.app/profile/pixelforge.games/post/demo1",
                                                          "post_text": PEOPLE[pixel.handle][1][1]})
    event(1, log.POST_SENT, "success", "Sent a new post from @mira-draws.bsky.social to \"art-drops\".",
          account=mira, channel=art, attempt=1, detail={"post_url": "https://bsky.app/profile/mira-draws.bsky.social"
                                                        "/post/demo2", "post_text": PEOPLE[mira.handle][1][0]})
    if scenario == "problem":
        event(6, log.LOGIN_FAILED, "error", "Bluesky didn't accept the bot's login. Checks are paused until it works.",
              category="bluesky_login_rejected")


# -- server ---------------------------------------------------------------------------------------
def _demo_urls():
    from django.http import JsonResponse
    from django.urls import path

    def reset(request, scenario):
        if scenario not in ("empty", "working", "problem"):
            return JsonResponse({"error": "unknown scenario"}, status=404)
        _reset(scenario)
        return JsonResponse({"reset": scenario})

    def discord(request):
        with _lock:
            return JsonResponse({"messages": list(DISCORD)})

    def new_post(request, handle):
        if handle not in PEOPLE:
            return JsonResponse({"error": "unknown account"}, status=404)
        texts = FEEDS.setdefault(handle, list(PEOPLE[handle][1]))
        texts.insert(0, request.GET.get("text") or f"A brand new post from {PEOPLE[handle][0]} ({len(texts) + 1}).")
        return JsonResponse({"posts": len(texts)})

    return [path("__demo__/reset/<str:scenario>/", reset), path("__demo__/discord/", discord),
            path("__demo__/post/<str:handle>/", new_post)]


def _reset(scenario):
    from django.core.management import call_command

    from bluesky_monitor import scheduler

    scheduler.stop()
    call_command("flush", interactive=False, verbosity=0)
    from django.core.cache import cache
    cache.clear()
    seed(scenario)
    scheduler.start()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--scenario", default="working", choices=["empty", "working", "problem"])
    args = parser.parse_args()

    data_dir = Path(tempfile.mkdtemp(prefix="alertbot-demo-"))
    _isolate(data_dir)
    _block_network()
    import django

    django.setup()
    from django.conf import settings

    assert not settings.BLUESKY_PASSWORD and not settings.DISCORD_WEBHOOK_URL, "the demo never sees real credentials"
    assert Path(settings.DATABASES["default"]["NAME"]).parent == data_dir
    install_fakes()

    from django.core.management import call_command

    call_command("migrate", interactive=False, verbosity=0)
    seed(args.scenario)

    import config.urls

    config.urls.urlpatterns[:0] = _demo_urls()
    from bluesky_monitor import scheduler

    scheduler.start()
    print(f"Demo bot ({args.scenario}) on http://127.0.0.1:{args.port}/  data: {data_dir}", flush=True)
    try:
        call_command("runserver", f"127.0.0.1:{args.port}", use_reloader=False, use_threading=True,
                     skip_checks=True)
    finally:
        scheduler.stop()
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
