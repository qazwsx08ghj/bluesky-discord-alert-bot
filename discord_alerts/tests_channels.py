"""Named channels.

Migration of existing setups, routing, default changes, safe deletion, editing without
re-entering URLs, secret handling, and private files never being served.
Discord is always mocked.
"""
import logging
from io import StringIO
from pathlib import Path
from unittest import mock

import requests
from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.checks import run_checks
from django.core.exceptions import SuspiciousFileOperation
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APITestCase

from bluesky_monitor import poller
from bluesky_monitor.models import MonitoredAccount, SeenPost
from bluesky_monitor.tests import FakeSession, feed_item
from botconfig import store
from . import channels, sender
from .logfilter import RedactWebhooks, redact
from .models import DiscordWebhook
from .sender import DeliveryResult


def hook(n, token=None):
    return f"https://discord.com/api/webhooks/{n}/{token or 'tok' + 'x' * 30 + str(n)}"


def token_of(url):
    return url.rsplit("/", 1)[1]


A, B, C, D = hook(101), hook(102), hook(103), hook(104)
ALL_TOKENS = [token_of(u) for u in (A, B, C, D)]


class NoTokens:
    def assert_no_tokens(self, text):
        for token in ALL_TOKENS:
            self.assertNotIn(token, str(text))


# -- Migration of existing configurations --------------------------------------------
class MigrationTests(TransactionTestCase):
    """Builds a pre-step-4 database, migrates it, and checks every route is unchanged."""

    before = [("bluesky_monitor", "0002_delivery_state_and_locks"), ("discord_alerts", "0001_initial"),
              ("botconfig", "0001_bot_settings")]

    def migrate_to(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def migrate_to_latest(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def old_setup(self, settings_url=A, with_settings_row=True):
        old = self.migrate_to(self.before)
        Account = old.get_model("bluesky_monitor", "MonitoredAccount")
        Webhook = old.get_model("discord_alerts", "DiscordWebhook")
        Settings = old.get_model("botconfig", "BotSettings")
        if with_settings_row:
            Settings.objects.create(pk=1, discord_webhook_url=settings_url, version=3)
        Webhook.objects.create(name="#art", url=C)                 # saved in the list, URL reused below
        Webhook.objects.create(name="#stale", url=D, is_default=True)  # flag ignored by old routing
        Webhook.objects.create(name="#stale2", url=D)
        Webhook.objects.filter(name="#stale2").update(is_default=True)  # two defaults (old data)
        Account.objects.create(handle="own.bsky.social", discord_webhook_url=B)
        Account.objects.create(handle="listed.bsky.social", discord_webhook_url=C)
        Account.objects.create(handle="follows.bsky.social", discord_webhook_url="")
        Account.objects.create(handle="same-as-default.bsky.social", discord_webhook_url=settings_url or B)
        Account.objects.create(handle="shares.bsky.social", discord_webhook_url=B)
        return {a.handle: (a.discord_webhook_url or settings_url) for a in Account.objects.all()}

    def assert_routes(self, old_routes):
        for handle, old_url in old_routes.items():
            with self.subTest(handle=handle):
                channel = channels.route_for(MonitoredAccount.objects.get(handle=handle))
                self.assertEqual(channel.url if channel else "", old_url)

    def test_every_notification_keeps_its_destination(self):
        routes = self.old_setup()
        self.migrate_to_latest()
        self.assert_routes(routes)

    def test_accounts_get_channels_and_followers_follow_the_default(self):
        self.old_setup()
        self.migrate_to_latest()
        own = MonitoredAccount.objects.get(handle="own.bsky.social")
        self.assertEqual(own.channel.name, "Channel for @own.bsky.social")
        self.assertEqual(MonitoredAccount.objects.get(handle="shares.bsky.social").channel, own.channel,
                         "the same URL becomes one shared channel")
        self.assertEqual(MonitoredAccount.objects.get(handle="listed.bsky.social").channel.name, "#art",
                         "an existing channel with that URL is reused")
        self.assertIsNone(MonitoredAccount.objects.get(handle="follows.bsky.social").channel)
        explicit = MonitoredAccount.objects.get(handle="same-as-default.bsky.social")
        self.assertIsNotNone(explicit.channel, "an explicit URL stays explicit even if it equals the default")

    def test_the_saved_default_url_becomes_the_only_default(self):
        self.old_setup()
        self.migrate_to_latest()
        defaults = list(DiscordWebhook.objects.filter(is_default=True))
        self.assertEqual([d.url for d in defaults], [A])
        self.assertEqual(defaults[0].name, "Default channel")

    def test_no_default_url_means_still_nothing_sent_for_followers(self):
        routes = self.old_setup(settings_url="")
        self.migrate_to_latest()
        self.assertFalse(DiscordWebhook.objects.filter(is_default=True).exists())
        self.assert_routes(routes)

    def test_upgrading_straight_from_step_2_uses_the_env_default(self):
        with override_settings(DISCORD_WEBHOOK_URL=A):
            routes = self.old_setup(with_settings_row=False)
            self.migrate_to_latest()
            self.assert_routes(routes)
            store.get()  # the first import doesn't add a second default
        self.assertEqual(list(DiscordWebhook.objects.filter(is_default=True).values_list("url", flat=True)), [A])

    def test_migration_can_be_undone_and_the_old_columns_still_route(self):
        routes = self.old_setup()
        self.migrate_to_latest()
        old = self.migrate_to(self.before)
        Account = old.get_model("bluesky_monitor", "MonitoredAccount")
        Settings = old.get_model("botconfig", "BotSettings")
        settings_url = Settings.objects.get(pk=1).discord_webhook_url
        for account in Account.objects.all():
            self.assertEqual(account.discord_webhook_url or settings_url, routes[account.handle])
        self.migrate_to_latest()
        self.assert_routes(routes)


# -- Routing and default changes ------------------------------------------------------
class RoutingTests(TestCase):
    def setUp(self):
        self.default = DiscordWebhook.objects.create(name="#general", url=A)
        channels.make_default(self.default)
        self.other = DiscordWebhook.objects.create(name="#art", url=B)
        self.follower = MonitoredAccount.objects.create(handle="f.bsky.social", first_poll_done=True)
        self.explicit = MonitoredAccount.objects.create(handle="e.bsky.social", first_poll_done=True,
                                                        channel=self.other)
        self.pinned = MonitoredAccount.objects.create(handle="p.bsky.social", first_poll_done=True,
                                                      channel=self.default)  # chose #general on purpose

    def routes(self):
        return {a.handle: channels.route_for(a).name for a in MonitoredAccount.objects.order_by("handle")}

    def test_followers_use_the_default_and_explicit_accounts_their_channel(self):
        self.assertEqual(self.routes(), {"e.bsky.social": "#art", "f.bsky.social": "#general",
                                         "p.bsky.social": "#general"})

    def test_changing_the_default_moves_only_followers(self):
        new = DiscordWebhook.objects.create(name="#news", url=C)
        channels.make_default(new)
        self.assertEqual(self.routes(), {"e.bsky.social": "#art", "f.bsky.social": "#news",
                                         "p.bsky.social": "#general"})

    def test_making_another_channel_the_default_keeps_explicit_routes(self):
        new = DiscordWebhook.objects.create(name="#news", url=C)
        res = self.client.post(f"/api/webhooks/{new.id}/make_default/", content_type="application/json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.routes(), {"e.bsky.social": "#art", "f.bsky.social": "#news",
                                         "p.bsky.social": "#general"})
        self.assertTrue(DiscordWebhook.objects.filter(name="#general").exists(), "the old default is kept")

    def test_only_one_default_at_a_time(self):
        channels.make_default(self.other)
        self.assertEqual(list(DiscordWebhook.objects.filter(is_default=True).values_list("name", flat=True)),
                         ["#art"])

    def test_announcements_go_to_the_new_default_without_a_restart(self):
        sent = []
        with mock.patch("discord_alerts.sender.deliver_alert",
                        side_effect=lambda **kw: sent.append(kw["webhook_url"]) or DeliveryResult(True)):
            poller.poll_account(self.follower, session=FakeSession(feed_item("1")))
            channels.make_default(self.other)
            poller.poll_account(self.follower, session=FakeSession(feed_item("2"), feed_item("1")))
        self.assertEqual(sent, [A, B])

    def test_delivery_health_is_recorded_per_channel(self):
        with mock.patch("discord_alerts.sender.deliver_alert", return_value=DeliveryResult(False, "down")):
            poller.poll_account(self.explicit, session=FakeSession(feed_item("1")))
        other = DiscordWebhook.objects.get(pk=self.other.pk)
        self.assertEqual((other.last_delivery_ok, other.last_delivery_error), (False, "down"))
        self.assertIsNone(DiscordWebhook.objects.get(pk=self.default.pk).last_delivery_at)

    def test_database_blocks_two_defaults(self):
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            DiscordWebhook.objects.filter(pk=self.other.pk).update(is_default=True)


class AccountChannelApiTests(NoTokens, APITestCase):
    url = "/api/accounts/"

    def setUp(self):
        self.default = DiscordWebhook.objects.create(name="#general", url=A)
        channels.make_default(self.default)
        self.art = DiscordWebhook.objects.create(name="#art", url=B)

    def test_create_with_a_channel(self):
        res = self.client.post(self.url, {"handle": "a.bsky.social", "channel_id": self.art.id}, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["channel"], {"id": self.art.id, "name": "#art", "is_default": False})
        self.assertFalse(res.data["uses_default_channel"])
        self.assert_no_tokens(res.data)

    def test_create_without_a_channel_follows_the_default(self):
        res = self.client.post(self.url, {"handle": "a.bsky.social"}, format="json")
        self.assertTrue(res.data["uses_default_channel"])
        self.assertIsNone(res.data["channel"])
        self.assertNotIn("discord_webhook_url", res.data, "no longer part of the API")
        self.assertNotIn("seen_posts", res.data, "no longer in the list; see /posts/")

    def test_the_removed_url_field_is_refused_with_a_pointer(self):
        """Accounts pick a channel by channel_id; a webhook URL isn't silently ignored."""
        res = self.client.post(self.url, {"handle": "a.bsky.social", "discord_webhook_url": B}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("channel_id", res.data["discord_webhook_url"][0])
        self.assert_no_tokens(res.data)
        self.assertFalse(MonitoredAccount.objects.exists())

    def test_switch_back_to_the_default(self):
        acc = MonitoredAccount.objects.create(handle="a.bsky.social", channel=self.art)
        res = self.client.patch(f"{self.url}{acc.id}/", {"channel_id": None}, format="json")
        self.assertTrue(res.data["uses_default_channel"])
        acc.refresh_from_db()
        self.assertIsNone(acc.channel_id)

    def test_unknown_channel_is_rejected(self):
        res = self.client.post(self.url, {"handle": "a.bsky.social", "channel_id": 999}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("channel_id", res.data)

    def test_a_url_sent_by_an_old_client_is_never_echoed(self):
        res = self.client.post(self.url, {"handle": "a.bsky.social",
                                          "discord_webhook_url": "https://evil.example/hook/secret-token"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertNotIn("secret-token", str(res.data))


# -- Safe deletion -----------------------------------------------------------------
class SafeDeleteTests(APITestCase):
    url = "/api/webhooks/"

    def setUp(self):
        self.default = DiscordWebhook.objects.create(name="#general", url=A)
        channels.make_default(self.default)
        self.art = DiscordWebhook.objects.create(name="#art", url=B)
        self.news = DiscordWebhook.objects.create(name="#news", url=C)
        self.acc = MonitoredAccount.objects.create(handle="a.bsky.social", channel=self.art)
        self.follower = MonitoredAccount.objects.create(handle="f.bsky.social")

    def delete(self, channel, query=""):
        return self.client.delete(f"{self.url}{channel.id}/{query}")

    def test_channel_in_use_is_not_deleted(self):
        res = self.delete(self.art)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["code"], "channel_in_use")
        self.assertEqual(res.data["accounts"], [{"id": self.acc.id, "handle": "a.bsky.social"}])
        self.assertTrue(DiscordWebhook.objects.filter(pk=self.art.pk).exists())
        self.acc.refresh_from_db()
        self.assertEqual(self.acc.channel_id, self.art.id)

    def test_reassign_to_another_channel(self):
        self.assertEqual(self.delete(self.art, f"?reassign_to={self.news.id}").status_code, 204)
        self.acc.refresh_from_db()
        self.assertEqual(self.acc.channel_id, self.news.id)

    def test_reassign_to_the_default(self):
        self.assertEqual(self.delete(self.art, "?reassign_to=default").status_code, 204)
        self.acc.refresh_from_db()
        self.assertIsNone(self.acc.channel)

    def test_reassign_to_default_needs_a_default(self):
        channels.clear_default()
        res = self.delete(self.art, "?reassign_to=default")
        self.assertEqual((res.status_code, res.data["code"]), (409, "no_default_channel"))

    def test_default_channel_is_not_deleted(self):
        res = self.delete(self.default)
        self.assertEqual((res.status_code, res.data["code"]), (409, "channel_is_default"))
        self.assertEqual(self.delete(self.default, "?reassign_to=default").status_code, 409)

    def test_default_channel_hands_over_to_the_chosen_channel(self):
        pinned = MonitoredAccount.objects.create(handle="p.bsky.social", channel=self.default)
        self.assertEqual(self.delete(self.default, f"?reassign_to={self.news.id}").status_code, 204)
        self.news.refresh_from_db()
        self.assertTrue(self.news.is_default)
        pinned.refresh_from_db()
        self.assertEqual(pinned.channel_id, self.news.id)
        self.assertEqual(channels.route_for(self.follower).name, "#news")

    def test_bad_reassign_targets(self):
        self.assertEqual(self.delete(self.art, f"?reassign_to={self.art.id}").status_code, 400)
        self.assertEqual(self.delete(self.art, "?reassign_to=999").status_code, 400)
        self.assertEqual(self.delete(self.art, "?reassign_to=abc").status_code, 400)
        self.assertTrue(DiscordWebhook.objects.filter(pk=self.art.pk).exists())

    def test_unused_channel_is_deleted(self):
        self.assertEqual(self.delete(self.news).status_code, 204)

    def test_database_also_refuses(self):
        with self.assertRaises(ProtectedError):
            self.art.delete()

    def test_default_can_not_be_switched_off_directly(self):
        res = self.client.patch(f"{self.url}{self.default.id}/", {"is_default": False}, format="json")
        self.assertEqual(res.status_code, 400)
        self.default.refresh_from_db()
        self.assertTrue(self.default.is_default)

    def test_make_default_action(self):
        res = self.client.post(f"{self.url}{self.news.id}/make_default/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["is_default"])
        self.acc.refresh_from_db()
        self.assertEqual(self.acc.channel_id, self.art.id, "explicit assignment untouched")


# -- Editing without re-entering URLs ----------------------------------------------------
class EditChannelTests(NoTokens, APITestCase):
    url = "/api/webhooks/"

    def setUp(self):
        self.art = DiscordWebhook.objects.create(name="#art", url=B)
        self.acc = MonitoredAccount.objects.create(handle="a.bsky.social", channel=self.art)

    def test_rename_keeps_the_url(self):
        res = self.client.patch(f"{self.url}{self.art.id}/", {"name": "#artwork"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.art.refresh_from_db()
        self.assertEqual((self.art.name, self.art.url), ("#artwork", B))

    def test_masked_url_sent_back_keeps_the_url(self):
        shown = self.client.get(f"{self.url}{self.art.id}/").data
        res = self.client.put(f"{self.url}{self.art.id}/", {"name": "#art2", "url": shown["url_masked"],
                                                              "is_default": False}, format="json")
        self.assertEqual(res.status_code, 200)
        self.art.refresh_from_db()
        self.assertEqual(self.art.url, B)

    def test_a_new_url_is_used_by_the_accounts_of_that_channel(self):
        self.client.patch(f"{self.url}{self.art.id}/", {"url": D}, format="json")
        self.acc.refresh_from_db()
        self.assertEqual(channels.route_for(self.acc).url, D)

    def test_invalid_url_is_rejected_without_echoing_it(self):
        res = self.client.patch(f"{self.url}{self.art.id}/", {"url": "https://discord.com/not-a-hook/zzsecretzz"},
                                format="json")
        self.assertEqual(res.status_code, 400)
        self.assertNotIn("zzsecretzz", str(res.data))
        self.art.refresh_from_db()
        self.assertEqual(self.art.url, B)

    def test_new_channel_needs_a_url_and_a_unique_name(self):
        self.assertEqual(self.client.post(self.url, {"name": "#x"}, format="json").status_code, 400)
        self.assertEqual(self.client.post(self.url, {"name": "#ART", "url": C}, format="json").status_code, 400)
        self.assertEqual(self.client.post(self.url, {"name": "  ", "url": C}, format="json").status_code, 400)

    def test_responses_show_masked_urls_and_counts(self):
        data = self.client.get(self.url).data["results"][0]
        self.assertNotIn("url", data)
        self.assertEqual(data["url_masked"], f"https://discord.com/api/webhooks/102/****{B[-4:]}")
        self.assertEqual(data["account_count"], 1)
        self.assert_no_tokens(data)


# -- Secrets in responses, logs and errors -----------------------------------------------
class SecretHandlingTests(NoTokens, APITestCase):
    def setUp(self):
        self.default = DiscordWebhook.objects.create(name="#general", url=A)
        channels.make_default(self.default)
        self.acc = MonitoredAccount.objects.create(handle="a.bsky.social", first_poll_done=True,
                                                   channel=DiscordWebhook.objects.create(name="#art", url=B))

    def capture(self):
        """Log output exactly as the console handler writes it (with its filter)."""
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(RedactWebhooks())
        names = ("discord_alerts", "bluesky_monitor", "botconfig", "django", "urllib3")
        loggers = [logging.getLogger(n) for n in names]
        levels = [lg.level for lg in loggers]
        for lg in loggers:
            lg.addHandler(handler)
            lg.setLevel(logging.DEBUG)

        def undo():
            for lg, level in zip(loggers, levels):
                lg.removeHandler(handler)
                lg.setLevel(level)
        self.addCleanup(undo)
        return stream

    def test_no_api_response_contains_a_webhook_token(self):
        for url in ("/api/webhooks/", f"/api/webhooks/{self.default.id}/", "/api/accounts/",
                    f"/api/accounts/{self.acc.id}/", "/api/settings/"):
            with self.subTest(url=url):
                self.assert_no_tokens(self.client.get(url).content.decode())

    def test_failed_sends_never_log_or_store_the_url(self):
        logs = self.capture()
        http_error = requests.HTTPError(f"404 Client Error: Not Found for url: {B}?wait=true",
                                        response=mock.Mock(status_code=404))
        failures = [http_error, requests.ConnectionError(f"HTTPSConnectionPool: Max retries exceeded with url: {B}")]
        for exc in failures:
            with mock.patch("discord_alerts.sender.requests.post") as post:
                if isinstance(exc, requests.HTTPError):
                    post.return_value.status_code = 404
                    post.return_value.raise_for_status.side_effect = exc
                else:
                    post.side_effect = exc
                result = sender.deliver_alert(B, "a.bsky.social", "A", "t", "u")
            self.assertFalse(result.ok)
            self.assert_no_tokens(result.error)
        with mock.patch("discord_alerts.sender.requests.post", side_effect=failures[1]):
            poller.poll_account(self.acc, session=FakeSession(feed_item("1")))
        self.assert_no_tokens(logs.getvalue())
        self.assert_no_tokens(list(SeenPost.objects.values_list("last_error", flat=True)))
        self.assert_no_tokens(list(DiscordWebhook.objects.values_list("last_delivery_error", flat=True)))

    def test_safety_net_masks_urls_from_other_libraries(self):
        logs = self.capture()
        logging.getLogger("urllib3").warning("Retrying after error for %s", B)
        try:
            raise RuntimeError(f"boom while calling {A}")
        except RuntimeError:
            logging.getLogger("django").exception("Internal Server Error")
        self.assert_no_tokens(logs.getvalue())
        self.assertIn("/api/webhooks/102/****", logs.getvalue())

    def test_logging_config_applies_the_filter_to_every_console_log(self):
        self.assertIn("redact_webhooks", settings.LOGGING["handlers"]["console"]["filters"])
        for name in ("django", "bluesky_monitor", "discord_alerts", "botconfig", "urllib3", "apscheduler"):
            self.assertIn("console", settings.LOGGING["loggers"][name]["handlers"])
        self.assertEqual(redact(f"x {B} y"), "x https://discord.com/api/webhooks/102/**** y")

    def test_channel_text_never_shows_the_url(self):
        self.assert_no_tokens(str(self.default) + repr(self.default))

    def test_channel_changes_are_logged_without_urls(self):
        logs = self.capture()
        channels.make_default(DiscordWebhook.objects.create(name="#news", url=C))
        channels.delete_channel(self.default)
        self.assertIn("#news", logs.getvalue())
        self.assert_no_tokens(logs.getvalue())


# -- Private files are never served ------------------------------------------------------
class PrivateFileTests(TestCase):
    PATHS = ["/.env", "/db.sqlite3", "/backups/", "/backups/2026-10-21_before-step-4/.env",
             "/static/.env", "/static/db.sqlite3", "/static/../.env", "/static/..%2f.env",
             "/static/%2e%2e/db.sqlite3", "/media/.env", "/.env.before-db-settings", "/manage.py",
             "/config/settings.py"]

    def test_private_paths_are_not_found(self):
        for debug in (False, True):
            with override_settings(DEBUG=debug):
                for path in self.PATHS:
                    with self.subTest(path=path, debug=debug):
                        res = self.client.get(path)
                        self.assertEqual(res.status_code, 404)
                        body = b"".join(res) if res.streaming else res.content
                        self.assertNotIn(b"SECRET_KEY", body)
                        self.assertNotIn(b"SQLite format", body)

    def test_static_finders_cannot_reach_private_files(self):
        for name in (".env", "db.sqlite3", "../.env", "backups", "manage.py"):
            with self.subTest(name=name):
                try:
                    found = finders.find(name)
                except SuspiciousFileOperation:
                    found = None  # refused outright, which is also fine
                self.assertFalse(found)

    def test_startup_check_refuses_to_serve_private_folders(self):
        base = Path(settings.BASE_DIR)
        for override in ({"STATICFILES_DIRS": [base]}, {"STATIC_ROOT": str(base)},
                         {"MEDIA_ROOT": str(base / "backups")}, {"STATICFILES_DIRS": [("x", str(base))]}):
            with self.subTest(override=override), override_settings(**override):
                ids = [e.id for e in run_checks(tags=["security"])]
                self.assertIn("botconfig.E001", ids)

    def test_safe_static_folder_passes(self):
        with override_settings(STATIC_ROOT=str(Path(settings.BASE_DIR) / "staticfiles")):
            self.assertNotIn("botconfig.E001", [e.id for e in run_checks(tags=["security"])])
