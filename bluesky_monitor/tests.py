"""Regression tests for watched accounts and the poller.

Bluesky and Discord are always mocked; these tests never touch the network.
"""
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from .models import MonitoredAccount, SeenPost
from .poller import poll_account, poll_all_active_accounts

DEFAULT_HOOK = "https://discord.com/api/webhooks/1/default"
CUSTOM_HOOK = "https://discord.com/api/webhooks/2/custom"


def feed_item(rkey, text="hello"):
    uri = f"at://did:plc:abc/app.bsky.feed.post/{rkey}"
    return SimpleNamespace(post=SimpleNamespace(uri=uri, cid=f"cid-{rkey}", record=SimpleNamespace(text=text)))


def fake_client(*items):
    """A Bluesky client whose author feed returns items newest first, like the real API."""
    client = mock.Mock()
    client.get_author_feed.return_value = SimpleNamespace(feed=list(items))
    return client


@override_settings(DISCORD_WEBHOOK_URL=DEFAULT_HOOK, BLUESKY_HANDLE="me.bsky.social", BLUESKY_PASSWORD="pw")
class PollerTests(TestCase):
    def setUp(self):
        self.account = MonitoredAccount.objects.create(handle="artist.bsky.social", display_name="Artist")
        send = mock.patch("bluesky_monitor.poller.send_discord_alert", return_value=True)
        self.send = send.start()
        self.addCleanup(send.stop)

    def poll_with(self, *items):
        with mock.patch("bluesky_monitor.poller._get_client", return_value=fake_client(*items)):
            return poll_account(self.account)

    def test_first_poll_records_posts_without_announcing(self):
        count = self.poll_with(feed_item("b"), feed_item("a"))
        self.assertEqual(count, 0)
        self.send.assert_not_called()
        self.assertEqual(SeenPost.objects.filter(account=self.account).count(), 2)
        self.account.refresh_from_db()
        self.assertTrue(self.account.first_poll_done)

    def test_first_poll_with_no_posts_still_completes(self):
        self.assertEqual(self.poll_with(), 0)
        self.account.refresh_from_db()
        self.assertTrue(self.account.first_poll_done)

    def test_later_poll_announces_only_new_posts_oldest_first(self):
        self.poll_with(feed_item("a"))
        count = self.poll_with(feed_item("c", "third"), feed_item("b", "second"), feed_item("a"))
        self.assertEqual(count, 2)
        texts = [c.kwargs["text"] for c in self.send.call_args_list]
        self.assertEqual(texts, ["second", "third"])
        first = self.send.call_args_list[0].kwargs
        self.assertEqual(first["webhook_url"], DEFAULT_HOOK)
        self.assertEqual(first["display_name"], "Artist")
        self.assertEqual(first["post_url"], "https://bsky.app/profile/artist.bsky.social/post/b")

    def test_repeat_poll_does_not_announce_twice(self):
        self.poll_with(feed_item("a"))
        self.poll_with(feed_item("b"), feed_item("a"))
        self.assertEqual(self.poll_with(feed_item("b"), feed_item("a")), 0)
        self.assertEqual(self.send.call_count, 1)

    def test_account_webhook_overrides_default(self):
        self.account.discord_webhook_url = CUSTOM_HOOK
        self.account.save()
        self.poll_with()
        self.poll_with(feed_item("a"))
        self.assertEqual(self.send.call_args.kwargs["webhook_url"], CUSTOM_HOOK)

    def test_display_name_falls_back_to_handle(self):
        self.account.display_name = ""
        self.account.save()
        self.poll_with()
        self.poll_with(feed_item("a"))
        self.assertEqual(self.send.call_args.kwargs["display_name"], "artist.bsky.social")

    @override_settings(DISCORD_WEBHOOK_URL="")
    def test_no_webhook_records_post_but_sends_nothing(self):
        self.poll_with()
        self.assertEqual(self.poll_with(feed_item("a")), 0)
        self.send.assert_not_called()
        self.assertTrue(SeenPost.objects.filter(post_uri__endswith="/a").exists())

    def test_feed_error_returns_zero_and_changes_nothing(self):
        with mock.patch("bluesky_monitor.poller._get_client", side_effect=RuntimeError("login failed")):
            self.assertEqual(poll_account(self.account), 0)
        self.account.refresh_from_db()
        self.assertFalse(self.account.first_poll_done)
        self.assertFalse(SeenPost.objects.exists())

    def test_poll_all_skips_paused_accounts(self):
        MonitoredAccount.objects.create(handle="paused.bsky.social", is_active=False)
        with mock.patch("bluesky_monitor.poller.poll_account") as poll:
            poll_all_active_accounts()
        self.assertEqual([c.args[0].handle for c in poll.call_args_list], ["artist.bsky.social"])


class AccountApiTests(APITestCase):
    url = "/api/accounts/"

    def test_list_is_paginated_and_starts_empty(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["results"], [])

    def test_create_and_list(self):
        res = self.client.post(self.url, {"handle": "artist.bsky.social", "display_name": "Artist",
                                          "discord_webhook_url": ""}, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertTrue(res.data["is_active"])
        self.assertFalse(res.data["first_poll_done"])
        listed = self.client.get(self.url).data["results"]
        self.assertEqual([a["handle"] for a in listed], ["artist.bsky.social"])
        self.assertEqual(listed[0]["seen_posts"], [])

    def test_list_is_newest_first(self):
        for h in ("one.bsky.social", "two.bsky.social"):
            self.client.post(self.url, {"handle": h}, format="json")
        listed = self.client.get(self.url).data["results"]
        self.assertEqual([a["handle"] for a in listed], ["two.bsky.social", "one.bsky.social"])

    def test_duplicate_handle_is_rejected_with_field_error(self):
        MonitoredAccount.objects.create(handle="artist.bsky.social")
        res = self.client.post(self.url, {"handle": "artist.bsky.social"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("handle", res.data)

    def test_invalid_webhook_url_is_rejected(self):
        res = self.client.post(self.url, {"handle": "a.bsky.social", "discord_webhook_url": "not a url"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("discord_webhook_url", res.data)

    def test_pause_and_resume(self):
        acc = MonitoredAccount.objects.create(handle="artist.bsky.social")
        res = self.client.patch(f"{self.url}{acc.id}/", {"is_active": False}, format="json")
        self.assertEqual(res.status_code, 200)
        acc.refresh_from_db()
        self.assertFalse(acc.is_active)
        self.client.patch(f"{self.url}{acc.id}/", {"is_active": True}, format="json")
        acc.refresh_from_db()
        self.assertTrue(acc.is_active)

    def test_first_poll_done_is_read_only(self):
        acc = MonitoredAccount.objects.create(handle="artist.bsky.social")
        self.client.patch(f"{self.url}{acc.id}/", {"first_poll_done": True}, format="json")
        acc.refresh_from_db()
        self.assertFalse(acc.first_poll_done)

    def test_delete_removes_post_history(self):
        acc = MonitoredAccount.objects.create(handle="artist.bsky.social")
        SeenPost.objects.create(account=acc, post_uri="at://x/app.bsky.feed.post/1", post_cid="c")
        res = self.client.delete(f"{self.url}{acc.id}/")
        self.assertEqual(res.status_code, 204)
        self.assertFalse(SeenPost.objects.exists())

    def test_poll_now_reports_first_poll_then_counts(self):
        acc = MonitoredAccount.objects.create(handle="artist.bsky.social")
        with mock.patch("bluesky_monitor.views.poll_account", return_value=0) as poll:
            res = self.client.post(f"{self.url}{acc.id}/poll_now/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, {"new_posts_announced": 0, "was_first_poll": True})
        poll.assert_called_once()

        acc.first_poll_done = True
        acc.save()
        with mock.patch("bluesky_monitor.views.poll_account", return_value=3):
            res = self.client.post(f"{self.url}{acc.id}/poll_now/")
        self.assertEqual(res.data, {"new_posts_announced": 3, "was_first_poll": False})

    def test_poll_now_unknown_account_is_404(self):
        self.assertEqual(self.client.post(f"{self.url}999/poll_now/").status_code, 404)
