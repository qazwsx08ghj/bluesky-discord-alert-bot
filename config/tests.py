"""Regression tests for the settings API, removed routes, and migrations."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from botconfig import store

HOOK = "https://discord.com/api/webhooks/1/token"


class SettingsApiTests(APITestCase):
    url = "/api/settings/"

    def test_get_with_nothing_set_returns_defaults(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual({k: res.data[k] for k in ("bluesky_handle", "default_channel",
                                                     "poll_interval_minutes", "has_bluesky_password")},
                         {"bluesky_handle": "", "default_channel": None,
                          "poll_interval_minutes": 5, "has_bluesky_password": False})

    @override_settings(BLUESKY_HANDLE="me.bsky.social", BLUESKY_PASSWORD="secret",
                       DISCORD_WEBHOOK_URL=HOOK, POLL_INTERVAL_MINUTES="10")
    def test_get_shows_imported_settings_and_never_secrets(self):
        res = self.client.get(self.url)
        self.assertEqual(res.data["bluesky_handle"], "me.bsky.social")
        self.assertEqual(res.data["poll_interval_minutes"], 10)
        self.assertTrue(res.data["has_bluesky_password"])
        self.assertEqual(res.data["default_channel"]["name"], "Default channel")
        self.assertNotIn("secret", str(res.data))
        self.assertNotIn("token", str(res.data))
        for removed in ("discord_webhook_url", "has_discord_webhook_url", "env_file_in_sync"):
            self.assertNotIn(removed, res.data, "removed in step 10")

    def test_post_saves_every_field(self):
        res = self.client.post(self.url, {"bluesky_handle": "new.bsky.social", "bluesky_password": "pw",
                                          "poll_interval_minutes": 7}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["status"], "saved")
        self.assertNotIn("pw", str(res.data["settings"]))
        row = store.get()
        self.assertEqual((row.bluesky_handle, row.bluesky_password, row.poll_interval_minutes),
                         ("new.bsky.social", "pw", 7))

    def test_blank_password_keeps_the_saved_one(self):
        store.save({"bluesky_password": "secret"})
        self.client.post(self.url, {"bluesky_handle": "me.bsky.social", "bluesky_password": ""}, format="json")
        self.assertEqual(store.get().bluesky_password, "secret")

    def test_interval_string_is_accepted(self):
        self.client.post(self.url, {"poll_interval_minutes": "3"}, format="json")
        self.assertEqual(store.get().poll_interval_minutes, 3)

    def test_patch_is_accepted(self):
        res = self.client.patch(self.url, {"poll_interval_minutes": 9}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(store.get().poll_interval_minutes, 9)


class RemovedRoutesTests(APITestCase):
    def test_the_old_test_message_route_is_gone(self):
        """Step 10: use POST /api/webhooks/{id}/test/ instead."""
        res = self.client.post("/api/settings/test-discord/", {}, format="json")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["code"], "not_found")

    def test_the_old_page_is_gone(self):
        self.assertEqual(self.client.get("/classic/").status_code, 404)


class MigrationTests(TestCase):
    def test_models_and_migrations_are_in_sync(self):
        """Fails if a model changes without a committed migration."""
        out = StringIO()
        try:
            call_command("makemigrations", "--check", "--dry-run", stdout=out, stderr=out)
        except SystemExit:
            self.fail("Models changed without a migration:\n" + out.getvalue())
