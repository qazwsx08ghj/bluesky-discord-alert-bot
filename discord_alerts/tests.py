"""Regression tests for Discord webhooks and the sender. Discord is always mocked."""
from unittest import mock

import requests
from django.test import SimpleTestCase
from rest_framework.test import APITestCase

from .models import DiscordWebhook
from .sender import send_discord_alert

HOOK = "https://discord.com/api/webhooks/1/token"


class SenderTests(SimpleTestCase):
    @mock.patch("discord_alerts.sender.requests.post")
    def test_sends_embed_and_returns_true(self, post):
        post.return_value.raise_for_status.return_value = None
        ok = send_discord_alert(HOOK, "artist.bsky.social", "Artist", "hi", "https://bsky.app/x")
        self.assertTrue(ok)
        args, kwargs = post.call_args
        self.assertEqual(args[0], HOOK)
        embed = kwargs["json"]["embeds"][0]
        self.assertEqual(embed["title"], "New post from Artist")
        self.assertEqual(embed["description"], "hi")
        self.assertEqual(embed["url"], "https://bsky.app/x")
        self.assertEqual(embed["color"], 0x0085FF)
        self.assertEqual(embed["footer"]["text"], "@artist.bsky.social on Bluesky")
        self.assertEqual(kwargs["timeout"], 10)

    @mock.patch("discord_alerts.sender.requests.post")
    def test_empty_text_shows_placeholder(self, post):
        send_discord_alert(HOOK, "a", "A", "", "u")
        self.assertEqual(post.call_args.kwargs["json"]["embeds"][0]["description"], "(no text)")

    @mock.patch("discord_alerts.sender.requests.post", side_effect=requests.ConnectionError("down"))
    def test_network_error_returns_false(self, post):
        self.assertFalse(send_discord_alert(HOOK, "a", "A", "t", "u"))

    @mock.patch("discord_alerts.sender.requests.post")
    def test_http_error_returns_false(self, post):
        post.return_value.raise_for_status.side_effect = requests.HTTPError("404")
        self.assertFalse(send_discord_alert(HOOK, "a", "A", "t", "u"))


class WebhookApiTests(APITestCase):
    url = "/api/webhooks/"

    def test_crud(self):
        res = self.client.post(self.url, {"name": "#announcements", "url": HOOK}, format="json")
        self.assertEqual(res.status_code, 201)
        wid = res.data["id"]
        self.assertEqual(self.client.get(self.url).data["results"][0]["name"], "#announcements")
        res = self.client.patch(f"{self.url}{wid}/", {"name": "#news"}, format="json")
        self.assertEqual(res.data["name"], "#news")
        self.assertEqual(self.client.delete(f"{self.url}{wid}/").status_code, 204)

    def test_name_must_be_unique(self):
        DiscordWebhook.objects.create(name="#a", url=HOOK)
        res = self.client.post(self.url, {"name": "#a", "url": HOOK}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_only_one_default(self):
        first = DiscordWebhook.objects.create(name="#a", url=HOOK, is_default=True)
        self.client.post(self.url, {"name": "#b", "url": HOOK, "is_default": True}, format="json")
        first.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertEqual(list(DiscordWebhook.objects.filter(is_default=True).values_list("name", flat=True)), ["#b"])

    @mock.patch("discord_alerts.views.send_discord_alert", return_value=True)
    def test_test_action_sent(self, send):
        hook = DiscordWebhook.objects.create(name="#a", url=HOOK)
        res = self.client.post(f"{self.url}{hook.id}/test/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, {"status": "sent"})
        self.assertEqual(send.call_args.kwargs["webhook_url"], HOOK)

    @mock.patch("discord_alerts.views.send_discord_alert", return_value=False)
    def test_test_action_failed(self, send):
        hook = DiscordWebhook.objects.create(name="#a", url=HOOK)
        res = self.client.post(f"{self.url}{hook.id}/test/")
        self.assertEqual(res.status_code, 502)
        self.assertEqual(res.data, {"status": "failed"})
