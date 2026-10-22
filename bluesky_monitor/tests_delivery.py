"""Claim-before-send, retries, interrupted sends and feed reading (in one process).

Time is controlled with a fake clock so retry delays and stale timeouts can be tested
without waiting. Discord and Bluesky are always mocked.
"""
from datetime import datetime, timedelta, timezone as dt_tz
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from discord_alerts.sender import DeliveryResult
from . import poller
from .locks import acquire_account_lock
from .models import MonitoredAccount, PostDelivery, SeenPost
from .tests import FakeSession, feed_item

HOOK = "https://discord.com/api/webhooks/1/default"
T0 = datetime(2026, 10, 21, 12, 0, tzinfo=dt_tz.utc)


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


@override_settings(DISCORD_WEBHOOK_URL=HOOK, SEND_MAX_ATTEMPTS=4, SEND_RETRY_WINDOW_HOURS=24, SEND_STALE_SECONDS=120)
class DeliveryTests(TestCase):
    def setUp(self):
        self.clock = Clock()
        p = mock.patch("django.utils.timezone.now", self.clock)
        p.start()
        self.addCleanup(p.stop)
        self.account = MonitoredAccount.objects.create(handle="artist.bsky.social", first_poll_done=True)
        self.results = []
        p = mock.patch("discord_alerts.sender.deliver_alert", side_effect=self._deliver)
        self.send = p.start()
        self.addCleanup(p.stop)

    def _deliver(self, **kwargs):
        return self.results.pop(0) if self.results else DeliveryResult(True)

    def claim(self, *rkeys):
        for r in rkeys:
            self.assertTrue(poller.claim_post(self.account, feed_item(r), SeenPost.PENDING))

    def post(self, rkey):
        return SeenPost.objects.get(post_uri__endswith=f"/{rkey}")

    def delivery(self, rkey):
        """The post's one delivery (the account follows the default channel)."""
        return PostDelivery.objects.get(post__post_uri__endswith=f"/{rkey}")

    # -- claiming -------------------------------------------------------------
    def test_a_post_can_only_be_claimed_once(self):
        self.claim("a")
        self.assertFalse(poller.claim_post(self.account, feed_item("a"), SeenPost.PENDING))
        self.assertEqual(SeenPost.objects.count(), 1)

    def test_two_accounts_can_each_announce_the_same_post(self):
        other = MonitoredAccount.objects.create(handle="fan.bsky.social", first_poll_done=True)
        self.assertTrue(poller.claim_post(self.account, feed_item("a"), SeenPost.PENDING))
        self.assertTrue(poller.claim_post(other, feed_item("a"), SeenPost.PENDING))
        self.assertEqual(poller.deliver_pending(self.account) + poller.deliver_pending(other), 2)

    def test_lost_race_between_read_and_claim_sends_nothing(self):
        """Two deliverers read the same pending post; only the first conditional update wins."""
        self.claim("a")
        stale_view = list(poller._due_for_delivery(self.account, self.clock()))
        self.assertEqual(poller.deliver_pending(self.account), 1)
        with mock.patch.object(poller, "_due_for_delivery", return_value=stale_view):
            self.assertEqual(poller.deliver_pending(self.account), 0)
        self.assertEqual(self.send.call_count, 1)

    # -- retries --------------------------------------------------------------
    def test_failed_send_is_kept_and_retried_later(self):
        self.claim("a")
        self.results = [DeliveryResult(False, "Couldn't reach Discord.")]
        self.assertEqual(poller.deliver_pending(self.account), 0)
        delivery = self.delivery("a")
        self.assertEqual((delivery.status, delivery.attempts), (PostDelivery.RETRY, 1))
        self.assertEqual(delivery.next_attempt_at, T0 + timedelta(minutes=1))
        self.assertEqual(delivery.last_error, "Couldn't reach Discord.")
        self.assertEqual(self.post("a").status, SeenPost.PENDING, "the post waits while a delivery does")

        self.assertEqual(poller.deliver_pending(self.account), 0, "not before its retry time")
        self.clock.advance(minutes=1, seconds=1)
        self.assertEqual(poller.deliver_pending(self.account), 1)
        self.assertEqual(self.post("a").status, SeenPost.SENT)
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(poller.deliver_pending(self.account), 0, "never sent again")

    def test_retry_delays_grow_then_give_up_after_four_attempts(self):
        self.claim("a")
        fail = DeliveryResult(False, "Discord returned an error (500).")
        self.results = [fail] * 4
        for expected_delay in (1, 5, 30):
            poller.deliver_pending(self.account)
            self.assertEqual(self.delivery("a").next_attempt_at, self.clock() + timedelta(minutes=expected_delay))
            self.clock.advance(minutes=expected_delay)
        poller.deliver_pending(self.account)
        delivery = self.delivery("a")
        self.assertEqual((delivery.status, delivery.attempts), (PostDelivery.FAILED, 4))
        self.assertEqual(self.post("a").status, SeenPost.FAILED)
        self.clock.advance(hours=1)
        poller.deliver_pending(self.account)
        self.assertEqual(self.send.call_count, 4)

    def test_discord_rate_limit_wait_is_respected(self):
        self.claim("a")
        self.results = [DeliveryResult(False, "slow down", retry_after=600, status_code=429)]
        poller.deliver_pending(self.account)
        self.assertEqual(self.delivery("a").next_attempt_at, T0 + timedelta(seconds=600))

    def test_posts_older_than_the_retry_window_give_up_without_sending(self):
        self.claim("a")
        self.results = [DeliveryResult(False, "down")]
        poller.deliver_pending(self.account)
        self.clock.advance(hours=25)
        poller.deliver_pending(self.account)
        self.assertEqual(self.post("a").status, SeenPost.FAILED)
        self.assertEqual(self.send.call_count, 1)

    def test_a_failing_channel_pauses_the_rest_of_that_accounts_queue(self):
        self.claim("a", "b", "c")
        self.results = [DeliveryResult(False, "down")]
        poller.deliver_pending(self.account)
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual([self.delivery(r).status for r in "abc"],
                         [PostDelivery.RETRY, PostDelivery.PENDING, PostDelivery.PENDING])
        self.clock.advance(minutes=2)
        self.assertEqual(poller.deliver_pending(self.account), 3)
        texts = [c.kwargs["post_url"].rsplit("/", 1)[1] for c in self.send.call_args_list]
        self.assertEqual(texts, ["a", "a", "b", "c"], "order is kept")

    def test_unsent_posts_survive_a_restart(self):
        """State is only in the database, so a new process picks up where the old one stopped."""
        self.claim("a", "b")
        self.results = [DeliveryResult(False, "down")]
        poller.deliver_pending(self.account)
        fresh = MonitoredAccount.objects.get(pk=self.account.pk)  # as a restarted process would load it
        self.clock.advance(minutes=2)
        self.assertEqual(poller.deliver_pending(fresh), 2)

    # -- interrupted sends ----------------------------------------------------
    def test_send_in_progress_elsewhere_is_left_alone(self):
        self.claim("a")
        PostDelivery.objects.update(status=PostDelivery.SENDING, claimed_at=T0, claim_token="other", attempts=1)
        self.clock.advance(seconds=60)
        self.assertEqual(poller.deliver_pending(self.account), 0)
        self.send.assert_not_called()

    def test_send_interrupted_by_a_crash_is_retried_once_stale(self):
        self.claim("a")
        PostDelivery.objects.update(status=PostDelivery.SENDING, claimed_at=T0, claim_token="dead", attempts=1)
        self.clock.advance(seconds=121)
        self.assertEqual(poller.deliver_pending(self.account), 1)
        delivery = self.delivery("a")
        self.assertEqual((delivery.status, delivery.attempts), (PostDelivery.SENT, 2))
        self.assertEqual(self.post("a").status, SeenPost.SENT)
        self.assertEqual(poller.deliver_pending(self.account), 0)

    def test_a_late_result_from_a_taken_over_send_is_ignored(self):
        """If a stale send is taken over, the old sender's result can't overwrite the new state."""
        self.claim("a")
        old = PostDelivery.objects.get()
        PostDelivery.objects.update(status=PostDelivery.SENDING, claim_token="old-sender", claimed_at=T0)
        self.clock.advance(seconds=121)
        self.results = [DeliveryResult(False, "down")]
        poller.deliver_pending(self.account)  # new sender fails -> retry
        PostDelivery.objects.filter(pk=old.pk, claim_token="old-sender").update(status=PostDelivery.SENT)
        self.assertEqual(self.delivery("a").status, PostDelivery.RETRY)


@override_settings(DISCORD_WEBHOOK_URL=HOOK)
class FeedReadingTests(TestCase):
    def setUp(self):
        self.account = MonitoredAccount.objects.create(handle="artist.bsky.social", first_poll_done=True)
        p = mock.patch("discord_alerts.sender.deliver_alert", return_value=DeliveryResult(True))
        self.send = p.start()
        self.addCleanup(p.stop)

    def test_repost_link_uses_the_original_author(self):
        repost = feed_item("x", author="someone-else.bsky.social", did="did:plc:other")
        poller.poll_account(self.account, session=FakeSession(repost))
        self.assertEqual(self.send.call_args.kwargs["post_url"],
                         "https://bsky.app/profile/someone-else.bsky.social/post/x")

    def test_pages_are_read_until_a_known_post(self):
        poller.claim_post(self.account, feed_item("old"), SeenPost.SENT)
        pages = {
            None: SimpleNamespace(feed=[feed_item(str(n)) for n in range(60, 30, -1)], cursor="p2"),
            "p2": SimpleNamespace(feed=[feed_item(str(n)) for n in range(30, 0, -1)] + [feed_item("old")], cursor="p3"),
        }
        session = mock.Mock()
        session.author_feed.side_effect = lambda actor, cursor=None, limit=30: pages[cursor]
        self.assertEqual(poller.poll_account(self.account, session=session), 60)
        self.assertEqual(session.author_feed.call_count, 2, "stopped at the known post")
        sent = [c.kwargs["post_url"].rsplit("/", 1)[1] for c in self.send.call_args_list]
        self.assertEqual(sent, [str(n) for n in range(1, 61)], "oldest first")

    def test_paging_is_capped(self):
        page = lambda cursor: SimpleNamespace(feed=[feed_item(f"{cursor}-{n}") for n in range(30)], cursor=f"{cursor}+")
        session = mock.Mock()
        session.author_feed.side_effect = lambda actor, cursor=None, limit=30: page(cursor or "p")
        poller.poll_account(self.account, session=session)
        self.assertEqual(SeenPost.objects.count(), poller.FEED_MAX_ITEMS)

    def test_first_check_reads_one_page_and_announces_nothing(self):
        self.account.first_poll_done = False
        self.account.save()
        session = mock.Mock()
        session.author_feed.return_value = SimpleNamespace(feed=[feed_item("a")], cursor="more")
        self.assertEqual(poller.poll_account(self.account, session=session), 0)
        self.assertEqual(session.author_feed.call_count, 1)
        self.assertEqual(SeenPost.objects.get().status, SeenPost.SEEDED)
        self.send.assert_not_called()

    def test_busy_account_raises(self):
        acquire_account_lock(self.account.pk)
        with self.assertRaises(poller.AccountBusy):
            poller.poll_account(self.account, session=FakeSession())


class PollNowApiTests(APITestCase):
    def test_returns_409_while_the_account_is_being_checked(self):
        acc = MonitoredAccount.objects.create(handle="artist.bsky.social")
        acquire_account_lock(acc.pk)
        res = self.client.post(f"/api/accounts/{acc.id}/poll_now/")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["code"], "already_checking")

    @override_settings(BLUESKY_HANDLE="", BLUESKY_PASSWORD="")
    def test_missing_login_keeps_the_old_response_and_adds_the_reason(self):
        acc = MonitoredAccount.objects.create(handle="artist.bsky.social")
        res = self.client.post(f"/api/accounts/{acc.id}/poll_now/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["new_posts_announced"], 0)
        self.assertTrue(res.data["was_first_poll"])
        self.assertIn("login", res.data["error"])
        acc.refresh_from_db()
        self.assertFalse(acc.first_poll_done, "a failed first check is not marked done")
