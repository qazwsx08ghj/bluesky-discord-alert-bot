"""Regression tests for the control panel page, settings API, and migrations.

Settings tests write to a temporary .env, never the real one.
"""
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APITestCase

HOOK = "https://discord.com/api/webhooks/1/token"


class TempEnvMixin:
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env = Path(tmp.name) / ".env"
        patcher = mock.patch("config.frontend_views.ENV_PATH", self.env)
        patcher.start()
        self.addCleanup(patcher.stop)


class IndexTests(TestCase):
    def test_control_panel_page_renders(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Bluesky → Discord")


class SettingsApiTests(TempEnvMixin, APITestCase):
    url = "/api/settings/"

    def test_get_with_no_env_file_returns_defaults(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, {"bluesky_handle": "", "discord_webhook_url": "",
                                    "poll_interval_minutes": "5", "has_bluesky_password": False})

    def test_get_reads_env_and_never_returns_password(self):
        self.env.write_text("# comment\nBLUESKY_HANDLE=me.bsky.social\nBLUESKY_PASSWORD=secret\n"
                            f"DISCORD_WEBHOOK_URL={HOOK}\nPOLL_INTERVAL_MINUTES=10\n", encoding="utf-8")
        res = self.client.get(self.url)
        self.assertEqual(res.data["bluesky_handle"], "me.bsky.social")
        self.assertEqual(res.data["poll_interval_minutes"], "10")
        self.assertTrue(res.data["has_bluesky_password"])
        self.assertNotIn("secret", str(res.data))

    def test_post_updates_existing_keys_and_appends_new_ones(self):
        self.env.write_text("SECRET_KEY=keep-me\nBLUESKY_HANDLE=old.bsky.social\n", encoding="utf-8")
        res = self.client.post(self.url, {"bluesky_handle": "new.bsky.social", "bluesky_password": "pw",
                                          "discord_webhook_url": HOOK, "poll_interval_minutes": 7}, format="json")
        self.assertEqual(res.data, {"status": "saved"})
        text = self.env.read_text(encoding="utf-8")
        self.assertIn("SECRET_KEY=keep-me", text)
        self.assertIn("BLUESKY_HANDLE=new.bsky.social", text)
        self.assertNotIn("old.bsky.social", text)
        self.assertIn("BLUESKY_PASSWORD=pw", text)
        self.assertIn(f"DISCORD_WEBHOOK_URL={HOOK}", text)
        self.assertIn("POLL_INTERVAL_MINUTES=7", text)

    def test_blank_password_keeps_the_saved_one(self):
        self.env.write_text("BLUESKY_PASSWORD=secret\n", encoding="utf-8")
        self.client.post(self.url, {"bluesky_handle": "me.bsky.social", "bluesky_password": ""}, format="json")
        self.assertIn("BLUESKY_PASSWORD=secret", self.env.read_text(encoding="utf-8"))

    def test_post_creates_env_file_when_missing(self):
        self.client.post(self.url, {"poll_interval_minutes": 3}, format="json")
        self.assertIn("POLL_INTERVAL_MINUTES=3", self.env.read_text(encoding="utf-8"))


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
        self.env.write_text(f"DISCORD_WEBHOOK_URL={HOOK}\n", encoding="utf-8")
        self.client.post(self.url, {}, format="json")
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
