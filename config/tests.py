"""Regression tests for the control panel page, settings API, and migrations.

Settings tests write to a temporary .env, never the real one.
"""
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from botconfig import store

HOOK = "https://discord.com/api/webhooks/1/token"


class TempEnvMixin:
    """Points the .env copy at a temporary file, never the real one."""

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env = Path(tmp.name) / ".env"
        patcher = override_settings(SETTINGS_ENV_MIRROR_PATH=str(self.env))
        patcher.enable()
        self.addCleanup(patcher.disable)


class IndexTests(TestCase):
    def test_control_panel_page_renders(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Bluesky → Discord")


class SettingsApiTests(TempEnvMixin, APITestCase):
    """The same requests as before step 3; the settings now live in the database."""
    url = "/api/settings/"

    def test_get_with_nothing_set_returns_defaults(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual({k: res.data[k] for k in ("bluesky_handle", "discord_webhook_url",
                                                     "poll_interval_minutes", "has_bluesky_password")},
                         {"bluesky_handle": "", "discord_webhook_url": "",
                          "poll_interval_minutes": "5", "has_bluesky_password": False})

    @override_settings(BLUESKY_HANDLE="me.bsky.social", BLUESKY_PASSWORD="secret",
                       DISCORD_WEBHOOK_URL=HOOK, POLL_INTERVAL_MINUTES="10")
    def test_get_shows_imported_settings_and_never_secrets(self):
        res = self.client.get(self.url)
        self.assertEqual(res.data["bluesky_handle"], "me.bsky.social")
        self.assertEqual(res.data["poll_interval_minutes"], "10")
        self.assertTrue(res.data["has_bluesky_password"])
        self.assertTrue(res.data["has_discord_webhook_url"])
        self.assertNotIn("secret", str(res.data))
        self.assertNotIn("token", str(res.data), "the webhook token is masked")
        self.assertTrue(res.data["discord_webhook_url"].startswith("https://discord.com/api/webhooks/1/"))

    def test_post_saves_every_field(self):
        res = self.client.post(self.url, {"bluesky_handle": "new.bsky.social", "bluesky_password": "pw",
                                          "discord_webhook_url": HOOK, "poll_interval_minutes": 7}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["status"], "saved")
        self.assertNotIn("pw", str(res.data["settings"]))
        row = store.get()
        self.assertEqual((row.bluesky_handle, row.bluesky_password, row.discord_webhook_url,
                          row.poll_interval_minutes), ("new.bsky.social", "pw", HOOK, 7))

    def test_post_keeps_other_env_lines_in_the_rollback_copy(self):
        self.env.write_text("SECRET_KEY=keep-me\nBLUESKY_HANDLE=old.bsky.social\n", encoding="utf-8")
        self.client.post(self.url, {"bluesky_handle": "new.bsky.social", "poll_interval_minutes": 7}, format="json")
        text = self.env.read_text(encoding="utf-8")
        self.assertIn("SECRET_KEY=keep-me", text)
        self.assertIn("BLUESKY_HANDLE=new.bsky.social", text)
        self.assertNotIn("old.bsky.social", text)
        self.assertIn("POLL_INTERVAL_MINUTES=7", text)

    def test_blank_password_keeps_the_saved_one(self):
        store.save({"bluesky_password": "secret"})
        self.client.post(self.url, {"bluesky_handle": "me.bsky.social", "bluesky_password": ""}, format="json")
        self.assertEqual(store.get().bluesky_password, "secret")

    def test_masked_webhook_sent_back_means_unchanged(self):
        store.save({"discord_webhook_url": HOOK})
        shown = self.client.get(self.url).data["discord_webhook_url"]
        self.client.post(self.url, {"discord_webhook_url": shown}, format="json")
        self.assertEqual(store.get().discord_webhook_url, HOOK)

    def test_interval_string_is_accepted_like_before(self):
        self.client.post(self.url, {"poll_interval_minutes": "3"}, format="json")
        self.assertEqual(store.get().poll_interval_minutes, 3)

    def test_patch_is_accepted(self):
        res = self.client.patch(self.url, {"poll_interval_minutes": 9}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(store.get().poll_interval_minutes, 9)


class TestDiscordApiTests(TempEnvMixin, APITestCase):
    url = "/api/settings/test-discord/"

    def test_no_webhook_configured_is_400(self):
        res = self.client.post(self.url, {}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["status"], "error")

    @mock.patch("config.frontend_views.send_discord_alert", return_value=True)
    def test_uses_given_url(self, send):
        res = self.client.post(self.url, {"url": HOOK}, format="json")
        self.assertEqual(res.data, {"status": "sent"})
        self.assertEqual(send.call_args.kwargs["webhook_url"], HOOK)

    @mock.patch("config.frontend_views.send_discord_alert", return_value=True)
    def test_falls_back_to_saved_url(self, send):
        store.save({"discord_webhook_url": HOOK})
        self.client.post(self.url, {}, format="json")
        self.assertEqual(send.call_args.kwargs["webhook_url"], HOOK)

    @mock.patch("config.frontend_views.send_discord_alert", return_value=True)
    def test_masked_url_from_the_page_uses_the_saved_one(self, send):
        store.save({"discord_webhook_url": HOOK})
        masked = self.client.get("/api/settings/").data["discord_webhook_url"]
        self.client.post(self.url, {"url": masked}, format="json")
        self.assertEqual(send.call_args.kwargs["webhook_url"], HOOK)

    @mock.patch("config.frontend_views.send_discord_alert", return_value=False)
    def test_failure_is_502(self, send):
        res = self.client.post(self.url, {"url": HOOK}, format="json")
        self.assertEqual(res.status_code, 502)
        self.assertEqual(res.data["status"], "failed")


class MigrationTests(TestCase):
    def test_models_and_migrations_are_in_sync(self):
        """Fails if a model changes without a committed migration."""
        out = StringIO()
        try:
            call_command("makemigrations", "--check", "--dry-run", stdout=out, stderr=out)
        except SystemExit:
            self.fail("Models changed without a migration:\n" + out.getvalue())
