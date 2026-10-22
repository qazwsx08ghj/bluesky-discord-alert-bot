"""Activity log and status.

Covers status accuracy (every state, from the checker's own records), scheduler failures,
events written by checks and deliveries, pagination and filters, retention (and that it
never touches the delivery queue), secret handling, and the migrations.
Bluesky and Discord are always mocked; time is controlled with a fake clock.
"""
import json
from datetime import datetime, timedelta, timezone as dt_tz
from io import StringIO
from types import SimpleNamespace
from unittest import mock

import requests
from django.core.management import call_command
from django.db import DatabaseError, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APITestCase

from bluesky_monitor import poller, scheduler
from bluesky_monitor.bluesky import BlueskyLoginError, BlueskyNotConfigured, SharedSession, _fingerprint
from bluesky_monitor.models import BlueskySessionState, MonitoredAccount, SchedulerState, SeenPost
from bluesky_monitor.tests import FakeSession, feed_item
from botconfig.secrets import scrub
from discord_alerts import channels
from discord_alerts.models import DiscordWebhook
from discord_alerts.sender import DeliveryResult, deliver_alert
from . import log, problems, retention, status
from .models import ActivityEvent

T0 = datetime(2026, 10, 21, 12, 0, tzinfo=dt_tz.utc)
HANDLE = "bot.bsky.social"
PASSWORD = "abcd-efgh-ijkl-mnop"  # looks like a real App Password
TOKEN = "SuperSecretWebhookToken_0123456789abcdef"
WEBHOOK = f"https://discord.com/api/webhooks/123456/{TOKEN}"
OTHER_HOOK = "https://discord.com/api/webhooks/654321/OtherSecretToken_zyxwvutsrqponmlk"
SESSION = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJkaWQ6cGxjOmJvdCJ9.c2lnbmF0dXJlLXNlY3JldA"
CREDS = dict(BLUESKY_HANDLE=HANDLE, BLUESKY_PASSWORD=PASSWORD)


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


class ClockMixin:
    def start_clock(self):
        self.clock = Clock()
        p = mock.patch("django.utils.timezone.now", self.clock)
        p.start()
        self.addCleanup(p.stop)


def checker_running(now, **overrides):
    """The SchedulerState a healthy checker leaves behind: fresh heartbeat, last check a
    minute ago, next one in three minutes."""
    values = dict(instance_id="host:1:abc", heartbeat_at=now - timedelta(seconds=10),
                  lease_expires_at=now + timedelta(seconds=80), started_at=now - timedelta(hours=1),
                  cycle_started_at=now - timedelta(minutes=2), cycle_finished_at=now - timedelta(minutes=1),
                  next_run_at=now + timedelta(minutes=3),
                  last_cycle_summary={"result": "ok", "category": "", "accounts_checked": 1,
                                      "accounts_failed": 0, "accounts_busy": 0, "posts_sent": 0})
    values.update(overrides)
    SchedulerState.objects.update_or_create(pk=1, defaults=values)


def login_ok():
    BlueskySessionState.objects.update_or_create(pk=1, defaults=dict(
        handle=HANDLE, credential_fingerprint=_fingerprint(HANDLE, PASSWORD), session_string=SESSION))


def all_text(data) -> str:
    return json.dumps(data, default=str)


# ============================================================================== status
@override_settings(**CREDS, SCHEDULER_LEASE_SECONDS=90, SCHEDULER_STALL_MINUTES=10,
                   SCHEDULER_OVERDUE_GRACE_SECONDS=120)
class StatusTests(ClockMixin, APITestCase):
    def setUp(self):
        self.start_clock()
        self.channel = DiscordWebhook.objects.create(name="#general", url=WEBHOOK)
        channels.make_default(self.channel)
        self.account = MonitoredAccount.objects.create(handle="artist.bsky.social", first_poll_done=True)
        login_ok()

    def get(self):
        res = self.client.get("/api/status/")
        self.assertEqual(res.status_code, 200)
        return res.json()

    # -- the four states --------------------------------------------------------
    def test_running_normally(self):
        checker_running(self.clock.now)
        data = self.get()
        self.assertEqual(data["state"], "running")
        self.assertEqual(data["headline"], "Running normally")
        self.assertEqual(data["scheduler"]["state"], "running")
        self.assertEqual(data["scheduler"]["next_check_at"], (T0 + timedelta(minutes=3)).isoformat())
        self.assertEqual(data["scheduler"]["last_check"]["result"], "ok")
        self.assertEqual(data["bluesky_login"]["state"], "ok")
        self.assertEqual(data["problems"], [])
        self.assertTrue(data["setup"]["complete"])
        self.assertIn("Checking 1 account every 5 minutes", data["message"])

    def test_never_started_is_unavailable(self):
        data = self.get()
        self.assertEqual(data["state"], "unavailable")
        self.assertEqual(data["scheduler"]["state"], "not_started")
        self.assertEqual(data["scheduler"]["problem"]["fix"], "restart_bot")

    def test_stale_heartbeat_means_the_checker_stopped_even_though_the_api_answers(self):
        checker_running(self.clock.now)
        self.clock.advance(seconds=91)  # no heartbeat for longer than the lease
        data = self.get()
        self.assertEqual(data["state"], "unavailable")
        self.assertEqual(data["scheduler"]["state"], "stopped")
        self.assertFalse(data["scheduler"]["healthy"])
        self.assertIn("stopped responding", data["message"])
        self.assertIn("start.bat", data["message"])
        self.assertIsNone(data["scheduler"]["next_check_at"], "no promise of a next check")

    def test_heartbeat_just_inside_the_lease_is_still_running(self):
        checker_running(self.clock.now, heartbeat_at=self.clock.now - timedelta(seconds=89))
        self.assertEqual(self.get()["state"], "running")

    def test_a_check_stuck_for_too_long_is_stalled(self):
        checker_running(self.clock.now, cycle_started_at=self.clock.now - timedelta(minutes=11),
                        cycle_finished_at=self.clock.now - timedelta(minutes=16))
        data = self.get()
        self.assertEqual(data["state"], "unavailable")
        self.assertEqual(data["scheduler"]["state"], "stalled")
        self.assertIn("running for 11 minutes", data["scheduler"]["message"])

    def test_a_check_in_progress_is_healthy(self):
        checker_running(self.clock.now, cycle_started_at=self.clock.now - timedelta(seconds=20),
                        cycle_finished_at=self.clock.now - timedelta(minutes=5))
        data = self.get()
        self.assertEqual(data["state"], "running")
        self.assertEqual(data["scheduler"]["state"], "checking")
        self.assertTrue(data["scheduler"]["checking_now"])

    def test_overdue_check_is_stalled(self):
        checker_running(self.clock.now, next_run_at=self.clock.now - timedelta(minutes=3))
        data = self.get()
        self.assertEqual(data["scheduler"]["state"], "stalled")
        self.assertEqual(data["state"], "unavailable")

    def test_overdue_without_a_planned_time_uses_the_interval(self):
        checker_running(self.clock.now, next_run_at=None, cycle_started_at=self.clock.now - timedelta(minutes=6),
                        cycle_finished_at=self.clock.now - timedelta(minutes=6))
        self.assertEqual(self.get()["scheduler"]["state"], "running", "5 min + 2 min grace not reached")
        self.clock.advance(minutes=2)
        SchedulerState.objects.filter(pk=1).update(heartbeat_at=self.clock.now)
        self.assertEqual(self.get()["scheduler"]["state"], "stalled")

    def test_missing_login_is_not_configured(self):
        checker_running(self.clock.now)
        with override_settings(BLUESKY_HANDLE="", BLUESKY_PASSWORD=""):
            from botconfig.models import BotSettings
            BotSettings.objects.all().delete()
            data = self.get()
        self.assertEqual(data["state"], "not_configured")
        self.assertFalse(data["setup"]["bluesky_login"])
        self.assertEqual(data["setup"]["missing"][0]["fix"], "update_login")
        self.assertEqual(data["bluesky_login"]["state"], "not_configured")

    def test_no_channels_or_accounts_is_not_configured(self):
        checker_running(self.clock.now)
        MonitoredAccount.objects.all().delete()
        channels.clear_default()
        DiscordWebhook.objects.all().delete()
        data = self.get()
        self.assertEqual(data["state"], "not_configured")
        self.assertEqual({m["code"] for m in data["setup"]["missing"]}, {"no_channel", "no_accounts"})

    def test_checker_down_wins_over_unfinished_setup(self):
        MonitoredAccount.objects.all().delete()
        self.assertEqual(self.get()["state"], "unavailable")

    def test_rejected_login_is_a_problem_with_a_fix(self):
        checker_running(self.clock.now, last_cycle_summary={"result": "failed", "category": "bluesky_login_rejected"})
        BlueskySessionState.objects.filter(pk=1).update(
            session_string="", login_failures=1, login_retry_after=self.clock.now + timedelta(minutes=10),
            last_error="Bluesky didn't accept the handle or App Password.")
        data = self.get()
        self.assertEqual(data["state"], "problem")
        self.assertEqual(data["bluesky_login"]["state"], "failed")
        self.assertEqual(data["problems"][0]["category"], "bluesky_login_rejected")
        self.assertEqual(data["problems"][0]["fix"], "update_login")
        self.assertEqual(data["scheduler"]["last_check"]["result"], "failed")
        self.assertEqual(data["scheduler"]["last_check"]["problem"]["category"], "bluesky_login_rejected")

    def test_login_failure_for_old_credentials_is_not_reported(self):
        checker_running(self.clock.now)
        BlueskySessionState.objects.filter(pk=1).update(credential_fingerprint="old", login_failures=3)
        data = self.get()
        self.assertEqual(data["bluesky_login"]["state"], "unknown", "not tried with the new login yet")
        self.assertEqual(data["state"], "running")

    def test_account_that_cant_be_checked_is_a_problem_until_it_works(self):
        checker_running(self.clock.now)
        MonitoredAccount.objects.filter(pk=self.account.pk).update(
            last_check_ok=False, last_check_category="account_not_found", last_checked_at=self.clock.now)
        data = self.get()
        self.assertEqual(data["state"], "problem")
        problem = data["problems"][0]
        self.assertEqual(problem["category"], "account_not_found")
        self.assertEqual(problem["fix"], "check_handle")
        self.assertEqual(problem["account"]["handle"], "artist.bsky.social")
        MonitoredAccount.objects.filter(pk=self.account.pk).update(last_check_ok=True, last_check_category=None)
        self.assertEqual(self.get()["state"], "running", "problems come from current state, not the log")

    def test_paused_account_problems_are_ignored(self):
        checker_running(self.clock.now)
        MonitoredAccount.objects.filter(pk=self.account.pk).update(is_active=False, last_check_ok=False)
        data = self.get()
        self.assertEqual(data["state"], "running")
        self.assertIn("paused", data["message"])

    def test_failing_channel_is_a_problem_with_the_send_category(self):
        checker_running(self.clock.now)
        channels.record_delivery(self.channel.pk, False, "Discord didn't accept the webhook.")
        log.problem(log.SEND_FAILED, "discord_webhook_invalid", "x", channel=self.channel, level="warning")
        data = self.get()
        self.assertEqual(data["state"], "problem")
        self.assertEqual(data["problems"][0]["channel"]["name"], "#general")
        self.assertEqual(data["problems"][0]["fix"], "update_webhook")

    def test_followers_without_a_default_channel_are_a_problem(self):
        checker_running(self.clock.now)
        channels.clear_default()
        data = self.get()
        self.assertEqual(data["state"], "problem")
        self.assertEqual(data["problems"][0]["category"], "no_channel")

    def test_explicit_channel_accounts_dont_need_a_default(self):
        checker_running(self.clock.now)
        channels.assign(self.account, self.channel)
        channels.clear_default()
        self.assertEqual(self.get()["state"], "running")

    def test_temporary_bluesky_trouble_is_a_warning(self):
        checker_running(self.clock.now)
        MonitoredAccount.objects.filter(pk=self.account.pk).update(
            last_check_ok=False, last_check_category="bluesky_unavailable")
        problem = self.get()["problems"][0]
        self.assertEqual(problem["severity"], "warning")
        self.assertEqual(problem["fix"], "wait")

    def test_counts(self):
        checker_running(self.clock.now)
        MonitoredAccount.objects.create(handle="paused.bsky.social", is_active=False)
        for rkey, state in [("a", SeenPost.PENDING), ("b", SeenPost.RETRY), ("c", SeenPost.SENDING),
                            ("d", SeenPost.SENT), ("e", SeenPost.FAILED)]:
            poller.claim_post(self.account, feed_item(rkey), state)
        SeenPost.objects.filter(status=SeenPost.SENT).update(sent_at=self.clock.now)
        counts = self.get()["counts"]
        self.assertEqual(counts["accounts_total"], 2)
        self.assertEqual(counts["accounts_active"], 1)
        self.assertEqual(counts["pending_deliveries"], 3)
        self.assertEqual(counts["deliveries_retrying"], 1)
        self.assertEqual(counts["sent_last_24h"], 1)

    def test_recent_errors_are_listed_newest_first(self):
        checker_running(self.clock.now)
        log.record("post_sent", "success", "fine")
        log.problem(log.CHECK_FAILED, "bluesky_unavailable", "first", level="warning")
        log.problem(log.CYCLE_FAILED, "internal", "second", technical="Traceback ...")
        errors = self.get()["recent_errors"]
        self.assertEqual([e["message"] for e in errors], ["second", "first"])
        self.assertNotIn("technical", errors[0], "technical details only in the single-event view")

    def test_status_never_contains_secrets(self):
        checker_running(self.clock.now)
        channels.record_delivery(self.channel.pk, False, "Discord didn't accept the webhook.")
        log.problem(log.SEND_FAILED, "discord_webhook_invalid", f"failed for {WEBHOOK} with {PASSWORD}",
                    channel=self.channel, technical=f"session={SESSION}")
        text = all_text(self.get())
        for secret in (TOKEN, PASSWORD, SESSION):
            self.assertNotIn(secret, text)


# ====================================================================== scheduler & checks
@override_settings(**CREDS)
class SchedulerFailureTests(ClockMixin, TestCase):
    def setUp(self):
        self.start_clock()
        scheduler._active.clear()

    def test_unexpected_error_is_recorded_and_reported(self):
        with mock.patch("bluesky_monitor.poller.poll_all_active_accounts", side_effect=RuntimeError("boom")):
            self.assertTrue(scheduler.run_cycle("A"))
        state = SchedulerState.objects.get(pk=1)
        self.assertEqual(state.last_cycle_summary["result"], "failed")
        self.assertEqual(state.last_cycle_summary["category"], "internal")
        event = ActivityEvent.objects.get(type=log.CYCLE_FAILED)
        self.assertEqual(event.level, "error")
        self.assertIn("RuntimeError: boom", event.technical)
        self.assertNotIn("Traceback", event.message, "the user sees plain language")

    def test_repeated_failures_are_counted_on_one_event(self):
        with mock.patch("bluesky_monitor.poller.poll_all_active_accounts", side_effect=RuntimeError("boom")):
            for _ in range(3):
                scheduler.run_cycle("A")
                self.clock.advance(minutes=5)
        event = ActivityEvent.objects.get(type=log.CYCLE_FAILED)
        self.assertEqual(event.repeat_count, 3)
        self.clock.advance(hours=1)
        with mock.patch("bluesky_monitor.poller.poll_all_active_accounts", side_effect=RuntimeError("boom")):
            scheduler.run_cycle("A")
        self.assertEqual(ActivityEvent.objects.filter(type=log.CYCLE_FAILED).count(), 2, "a new entry each hour")

    def test_summary_of_a_partial_check(self):
        ok = MonitoredAccount.objects.create(handle="ok.bsky.social", first_poll_done=True)
        MonitoredAccount.objects.create(handle="gone.bsky.social", first_poll_done=True)
        from atproto_client import exceptions as at_exc
        not_found = at_exc.BadRequestError(SimpleNamespace(
            status_code=400, content=SimpleNamespace(error="InvalidRequest", message="Profile not found")))

        def feed(actor, cursor=None, limit=30):
            if actor == "gone.bsky.social":
                raise not_found
            return SimpleNamespace(feed=[], cursor=None)

        session = mock.Mock()
        session.author_feed.side_effect = feed
        with mock.patch("bluesky_monitor.poller._new_session", return_value=session):
            scheduler.run_cycle("A")
        summary = SchedulerState.objects.get(pk=1).last_cycle_summary
        self.assertEqual(summary["result"], "partial")
        self.assertEqual(summary["accounts_checked"], 2)
        self.assertEqual(summary["accounts_failed"], 1)
        self.assertEqual(summary["category"], "account_not_found")
        ok.refresh_from_db()
        self.assertTrue(ok.last_check_ok)
        gone = MonitoredAccount.objects.get(handle="gone.bsky.social")
        self.assertFalse(gone.last_check_ok)
        event = ActivityEvent.objects.get(type=log.CHECK_FAILED)
        self.assertEqual((event.category, event.level, event.account_id), ("account_not_found", "error", gone.pk))
        self.assertIn("BadRequestError", event.technical)

    def test_missing_login_stops_the_check_as_not_configured(self):
        MonitoredAccount.objects.create(handle="a.bsky.social")
        with mock.patch("bluesky_monitor.poller.poll_account", side_effect=BlueskyNotConfigured()):
            scheduler.run_cycle("A")
        summary = SchedulerState.objects.get(pk=1).last_cycle_summary
        self.assertEqual((summary["result"], summary["category"]), ("not_configured", "bluesky_not_configured"))

    def test_rejected_login_stops_the_check_as_failed(self):
        MonitoredAccount.objects.create(handle="a.bsky.social")
        with mock.patch("bluesky_monitor.poller.poll_account", side_effect=BlueskyLoginError("no")):
            scheduler.run_cycle("A")
        summary = SchedulerState.objects.get(pk=1).last_cycle_summary
        self.assertEqual((summary["result"], summary["category"]), ("failed", "bluesky_login_rejected"))

    def test_lost_lease_is_reported_as_interrupted(self):
        MonitoredAccount.objects.create(handle="a.bsky.social")
        MonitoredAccount.objects.create(handle="b.bsky.social")

        def poll(account, session=None):
            SchedulerState.objects.filter(pk=1).update(instance_id="someone-else")
            return 0

        scheduler.heartbeat("A")
        with mock.patch("bluesky_monitor.poller.poll_account", side_effect=poll):
            summary = poller.poll_all_active_accounts(
                should_continue=lambda: SchedulerState.objects.filter(instance_id="A").exists())
            self.assertEqual((summary.result, summary.accounts_checked), ("interrupted", 1))
            SchedulerState.objects.filter(pk=1).update(instance_id="A")
            scheduler.run_cycle("A")
        # The other checker now owns the row, so A doesn't overwrite its summary.
        self.assertIsNone(SchedulerState.objects.get(pk=1).last_cycle_summary)

    def test_checker_start_and_takeover_are_logged(self):
        self.assertTrue(scheduler.heartbeat("A"))
        self.assertTrue(scheduler.heartbeat("A"))
        self.assertEqual(ActivityEvent.objects.filter(type=log.CHECKER_STARTED).count(), 1, "once per start")
        first_start = SchedulerState.objects.get(pk=1).started_at
        self.assertFalse(scheduler.heartbeat("B"), "standby")
        self.clock.advance(seconds=200)  # A died
        self.assertTrue(scheduler.heartbeat("B"))
        self.assertEqual(ActivityEvent.objects.filter(type=log.CHECKER_STARTED).count(), 2)
        self.assertGreater(SchedulerState.objects.get(pk=1).started_at, first_start, "takeover time recorded")

    def test_status_follows_a_real_check(self):
        DiscordWebhook.objects.create(name="#general", url=WEBHOOK, is_default=True)
        MonitoredAccount.objects.create(handle="a.bsky.social", first_poll_done=True)
        login_ok()
        scheduler.heartbeat("A")
        with mock.patch("bluesky_monitor.poller._new_session", return_value=FakeSession(feed_item("n"))), \
                mock.patch("discord_alerts.sender.deliver_alert", return_value=DeliveryResult(True)):
            scheduler.run_cycle("A")
        data = status.compute()
        self.assertEqual(data["scheduler"]["last_check"]["result"], "ok")
        self.assertEqual(data["scheduler"]["last_check"]["posts_sent"], 1)
        self.clock.advance(minutes=2)  # heartbeats stop
        self.assertEqual(status.compute()["state"], "unavailable")

    def test_clean_up_failure_never_stops_the_checker(self):
        with mock.patch("bluesky_monitor.poller.poll_all_active_accounts",
                        return_value=poller.CycleSummary()), \
                mock.patch("activity.retention.prune", side_effect=DatabaseError("locked")):
            self.assertTrue(scheduler.run_cycle("A"))

    def test_log_failure_never_stops_a_delivery(self):
        DiscordWebhook.objects.create(name="#general", url=WEBHOOK, is_default=True)
        account = MonitoredAccount.objects.create(handle="a.bsky.social", first_poll_done=True)
        with mock.patch("activity.models.ActivityEvent.objects.create", side_effect=DatabaseError("full")), \
                mock.patch("discord_alerts.sender.deliver_alert", return_value=DeliveryResult(True)):
            self.assertEqual(poller.poll_account(account, session=FakeSession(feed_item("n"))), 1)
        self.assertEqual(SeenPost.objects.get().status, SeenPost.SENT)


@override_settings(**CREDS)
class LoginEventTests(ClockMixin, TestCase):
    def setUp(self):
        from bluesky_monitor.tests_session import BAD_PASSWORD, FakeClient
        self.start_clock()
        FakeClient.instances, FakeClient.login_errors, FakeClient.feed_errors = [], [BAD_PASSWORD], []
        p = mock.patch.object(SharedSession, "client_factory", FakeClient)
        p.start()
        self.addCleanup(p.stop)

    def test_rejected_login_then_recovery(self):
        with self.assertRaises(BlueskyLoginError) as ctx:
            SharedSession().author_feed("a")
        self.assertEqual(ctx.exception.category, "bluesky_login_rejected")
        event = ActivityEvent.objects.get(type=log.LOGIN_FAILED)
        self.assertEqual((event.level, event.category), ("error", "bluesky_login_rejected"))
        self.assertEqual(problems.get(event.category).fix, "update_login")
        self.clock.advance(minutes=11)
        SharedSession().author_feed("a")
        self.assertTrue(ActivityEvent.objects.filter(type=log.LOGIN_RECOVERED).exists())

    def test_waiting_after_a_rate_limit_is_categorised_as_such(self):
        BlueskySessionState.objects.update_or_create(pk=1, defaults=dict(
            credential_fingerprint=_fingerprint(HANDLE, PASSWORD), login_failures=0,
            login_retry_after=self.clock.now + timedelta(minutes=15), last_error="Bluesky is limiting logins."))
        with self.assertRaises(BlueskyLoginError) as ctx:
            SharedSession().author_feed("a")
        self.assertEqual(ctx.exception.category, "bluesky_rate_limited")


# ====================================================================== events from posts
@override_settings(**CREDS, SEND_MAX_ATTEMPTS=4, SEND_RETRY_WINDOW_HOURS=24)
class DeliveryEventTests(ClockMixin, TestCase):
    def setUp(self):
        self.start_clock()
        self.channel = DiscordWebhook.objects.create(name="#art", url=WEBHOOK, is_default=True)
        self.account = MonitoredAccount.objects.create(handle="artist.bsky.social", first_poll_done=True)
        self.results = []
        p = mock.patch("discord_alerts.sender.deliver_alert",
                       side_effect=lambda **kw: self.results.pop(0) if self.results else DeliveryResult(True))
        p.start()
        self.addCleanup(p.stop)

    def poll(self, *items):
        return poller.poll_account(self.account, session=FakeSession(*items))

    def events(self, **filters):
        return list(ActivityEvent.objects.filter(**filters).order_by("id"))

    def test_sent_post_is_logged_with_its_link(self):
        self.poll(feed_item("p1", text="Hello world"))
        event = self.events(type=log.POST_SENT)[0]
        self.assertEqual(event.level, "success")
        self.assertEqual(event.attempt, 1)
        self.assertEqual((event.account_label, event.channel_label), ("@artist.bsky.social", "#art"))
        self.assertEqual(event.detail["post_url"], "https://bsky.app/profile/artist.bsky.social/post/p1")
        self.assertEqual(event.detail["post_text"], "Hello world")
        self.assertIn('Sent a post from @artist.bsky.social to "#art"', event.message)

    def test_every_retry_attempt_is_logged_until_it_works(self):
        bad = DeliveryResult(False, "Couldn't reach Discord.", category="discord_unavailable",
                             technical="ConnectionError")
        self.results = [bad, bad]
        self.poll(feed_item("p1"))
        self.clock.advance(minutes=2)
        poller.deliver_pending(self.account)
        self.clock.advance(minutes=6)
        poller.deliver_pending(self.account)
        failed = self.events(type=log.SEND_FAILED)
        self.assertEqual([e.attempt for e in failed], [1, 2])
        self.assertTrue(all(e.level == "warning" and e.category == "discord_unavailable" for e in failed))
        self.assertIn("try again in about 1 minute", failed[0].message)
        self.assertIn("next_attempt_at", failed[0].detail)
        sent = self.events(type=log.POST_SENT)[0]
        self.assertEqual(sent.attempt, 3)
        self.assertIn("worked on try 3 of 4", sent.message)

    def test_giving_up_is_an_error(self):
        bad = DeliveryResult(False, "Discord didn't accept the webhook.", status_code=404,
                             category="discord_webhook_invalid", technical="HTTP 404: Unknown Webhook")
        self.results = [bad] * 4
        self.poll(feed_item("p1"))
        for minutes in (2, 6, 31):
            self.clock.advance(minutes=minutes)
            poller.deliver_pending(self.account)
        gave_up = self.events(type=log.SEND_GAVE_UP)
        self.assertEqual(len(gave_up), 1)
        self.assertEqual((gave_up[0].level, gave_up[0].attempt, gave_up[0].category),
                         ("error", 4, "discord_webhook_invalid"))
        self.assertEqual(gave_up[0].detail["http_status"], 404)
        self.assertEqual(SeenPost.objects.get().status, SeenPost.FAILED)

    def test_posts_too_old_to_send_are_logged(self):
        poller.claim_post(self.account, feed_item("old"), SeenPost.RETRY)
        SeenPost.objects.update(seen_at=self.clock.now - timedelta(hours=25), next_attempt_at=self.clock.now)
        poller.deliver_pending(self.account)
        self.assertEqual(self.events(type=log.SEND_GAVE_UP)[0].category, "send_expired")

    def test_first_check_reports_how_many_posts_were_noted(self):
        self.account.first_poll_done = False
        self.account.save()
        self.poll(feed_item("a"), feed_item("b"), feed_item("c"))
        event = self.events(type=log.FIRST_CHECK_DONE)[0]
        self.assertEqual(event.detail, {"seeded_posts": 3})
        self.assertIn("Noted 3 existing posts; none were sent", event.message)

    def test_first_check_of_an_empty_account_is_reported_too(self):
        self.account.first_poll_done = False
        self.account.save()
        self.poll()
        self.assertIn("Noted 0 existing posts", self.events(type=log.FIRST_CHECK_DONE)[0].message)

    def test_posts_without_a_channel_are_reported_once_an_hour(self):
        channels.clear_default()
        self.poll(feed_item("a"))
        self.poll(feed_item("b"))
        events = self.events(type=log.POST_NOT_SENT)
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0].repeat_count, events[0].category), (2, "no_channel"))

    def test_check_failure_then_recovery(self):
        from bluesky_monitor.bluesky import BlueskyUnavailable
        poller.poll_account(self.account, session=FakeSession(error=BlueskyUnavailable("timeout")))
        failed = self.events(type=log.CHECK_FAILED)[0]
        self.assertEqual((failed.level, failed.category), ("warning", "bluesky_unavailable"))
        self.poll()
        self.assertEqual(len(self.events(type=log.CHECK_RECOVERED)), 1)
        self.poll()
        self.assertEqual(len(self.events(type=log.CHECK_RECOVERED)), 1, "only after a failure")

    def test_account_added_and_removed_through_the_api(self):
        from rest_framework.test import APIClient
        client = APIClient()
        res = client.post("/api/accounts/", {"handle": "new.bsky.social"}, format="json")
        self.assertEqual(res.status_code, 201)
        client.delete(f"/api/accounts/{res.data['id']}/")
        added, removed = self.events(type=log.ACCOUNT_ADDED)[0], self.events(type=log.ACCOUNT_REMOVED)[0]
        self.assertEqual(added.account_id, removed.account_id)
        self.assertEqual(removed.account_label, "@new.bsky.social", "the name is kept after deletion")

    @mock.patch("discord_alerts.views.send_discord_alert", return_value=False)
    def test_channel_test_failure_is_logged(self, _send):
        from rest_framework.test import APIClient
        APIClient().post(f"/api/webhooks/{self.channel.pk}/test/")
        event = self.events(type=log.CHANNEL_TEST_FAILED)[0]
        self.assertEqual((event.category, event.channel_id), ("discord_test_failed", self.channel.pk))


# ====================================================================== the activity API
class ActivityApiTests(ClockMixin, APITestCase):
    url = "/api/activity/"

    def setUp(self):
        self.start_clock()

    def add(self, n, type="post_sent", level="success", **kw):
        for i in range(n):
            log.record(type, level, f"{type} {i}", **kw)
            self.clock.advance(seconds=1)

    def test_newest_first_with_default_page_size(self):
        self.add(60)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data["results"]), 50)
        ids = [e["id"] for e in res.data["results"]]
        self.assertEqual(ids, sorted(ids, reverse=True))
        self.assertIsNotNone(res.data["next"])

    def test_paging_is_stable_while_new_events_arrive(self):
        self.add(30)
        first = self.client.get(self.url, {"page_size": 10}).data
        self.add(5)  # new events arrive while the user reads page 1
        seen = [e["id"] for e in first["results"]]
        next_url = first["next"]
        while next_url:
            page = self.client.get(next_url).data
            seen += [e["id"] for e in page["results"]]
            next_url = page["next"]
        self.assertEqual(len(seen), 30, "no duplicates, nothing skipped")
        self.assertEqual(len(set(seen)), 30)

    def test_page_size_is_capped(self):
        self.add(210)
        self.assertEqual(len(self.client.get(self.url, {"page_size": 500}).data["results"]), 200)

    def test_group_filters(self):
        account = MonitoredAccount.objects.create(handle="a.bsky.social")
        log.record(log.POST_SENT, "success", "sent first try", attempt=1)
        log.record(log.POST_SENT, "success", "sent on retry", attempt=2)
        log.problem(log.SEND_FAILED, "discord_unavailable", "send failed", attempt=1, level="warning")
        log.problem(log.SEND_GAVE_UP, "discord_webhook_invalid", "gave up", attempt=4)
        log.problem(log.CHECK_FAILED, "account_not_found", "check failed", account=account)
        log.problem(log.LOGIN_FAILED, "bluesky_login_rejected", "login failed")
        log.record(log.ACCOUNT_ADDED, "info", "added")

        def messages(**params):
            res = self.client.get(self.url, params)
            self.assertEqual(res.status_code, 200, res.content)
            return {e["message"] for e in res.data["results"]}

        self.assertEqual(messages(group="sent"), {"sent first try", "sent on retry"})
        self.assertEqual(messages(group="failed_checks"), {"check failed", "login failed"})
        self.assertEqual(messages(group="failed_sends"), {"send failed", "gave up"})
        self.assertEqual(messages(group="retries"), {"sent on retry", "gave up"})
        self.assertEqual(messages(group="problems"), {"send failed", "gave up", "check failed", "login failed"})
        self.assertEqual(len(messages()), 7)
        self.assertEqual(messages(type="account_added,login_failed"), {"added", "login failed"})
        self.assertEqual(messages(level="warning"), {"send failed"})
        self.assertEqual(messages(account=account.pk), {"check failed"})
        self.assertEqual(messages(group="problems", level="error", account=account.pk), {"check failed"})

    def test_channel_and_time_filters(self):
        channel = DiscordWebhook.objects.create(name="#art", url=WEBHOOK)
        log.record(log.POST_SENT, "success", "old", channel=channel)
        self.clock.advance(days=2)
        log.record(log.POST_SENT, "success", "new", channel=channel)
        log.record(log.POST_SENT, "success", "elsewhere")
        res = self.client.get(self.url, {"channel": channel.pk, "since": (T0 + timedelta(days=1)).date().isoformat()})
        self.assertEqual([e["message"] for e in res.data["results"]], ["new"])
        res = self.client.get(self.url, {"before": (T0 + timedelta(hours=1)).isoformat()})
        self.assertEqual([e["message"] for e in res.data["results"]], ["old"])

    def test_bad_filters_are_explained(self):
        for params in ({"group": "nope"}, {"level": "loud"}, {"account": "abc"}, {"since": "yesterday"}):
            res = self.client.get(self.url, params)
            self.assertEqual(res.status_code, 400, params)
            self.assertEqual(res.json()["code"], "invalid_filter")
            self.assertIn(next(iter(params)), res.json()["fields"])

    def test_problem_explanation_in_list_and_technical_only_in_detail(self):
        event = log.problem(log.SEND_GAVE_UP, "discord_webhook_invalid", "gave up", technical="HTTP 404: Unknown Webhook")
        item = self.client.get(self.url).data["results"][0]
        self.assertEqual(item["problem"]["fix"], "update_webhook")
        self.assertIn("Integrations", item["problem"]["explanation"])
        self.assertNotIn("technical", item)
        detail = self.client.get(f"{self.url}{event.pk}/").data
        self.assertEqual(detail["technical"], "HTTP 404: Unknown Webhook")

    def test_log_is_read_only(self):
        self.assertEqual(self.client.post(self.url, {"type": "x"}, format="json").status_code, 405)
        event = log.record("post_sent", "success", "x")
        self.assertEqual(self.client.delete(f"{self.url}{event.pk}/").status_code, 405)

    def test_existing_routes_still_work(self):
        for url in ("/api/accounts/", "/api/webhooks/", "/api/settings/", "/api/status/", "/api/activity/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)


# ====================================================================== retention
@override_settings(ACTIVITY_RETENTION_DAYS=30, ACTIVITY_MAX_EVENTS=10_000)
class RetentionTests(ClockMixin, TestCase):
    def setUp(self):
        self.start_clock()

    def add(self, n, age=timedelta(0)):
        ActivityEvent.objects.bulk_create(
            [ActivityEvent(created_at=self.clock.now - age, type="post_sent", level="success", message="x")
             for _ in range(n)])

    def test_events_older_than_30_days_are_removed(self):
        self.add(3, age=timedelta(days=31))
        self.add(2, age=timedelta(days=29))
        self.assertEqual(retention.prune(), {"too_old": 3, "over_limit": 0})
        self.assertEqual(ActivityEvent.objects.count(), 2)

    def test_never_more_than_the_limit_newest_kept(self):
        self.add(5, age=timedelta(days=2))
        self.add(10)
        with override_settings(ACTIVITY_MAX_EVENTS=10):
            self.assertEqual(retention.prune(), {"too_old": 0, "over_limit": 5})
        self.assertEqual(ActivityEvent.objects.count(), 10)
        self.assertFalse(ActivityEvent.objects.filter(created_at__lt=self.clock.now).exists(), "oldest went first")

    def test_whichever_limit_is_reached_first(self):
        self.add(4, age=timedelta(days=40))
        self.add(8)
        with override_settings(ACTIVITY_MAX_EVENTS=5):
            self.assertEqual(retention.prune(), {"too_old": 4, "over_limit": 3})
        self.assertEqual(ActivityEvent.objects.count(), 5)

    def test_default_limits_are_30_days_and_10000_events(self):
        import config.settings as project_settings
        self.assertEqual(project_settings.ACTIVITY_RETENTION_DAYS, 30)
        self.assertEqual(project_settings.ACTIVITY_MAX_EVENTS, 10_000)

    def test_full_size_log_is_trimmed_to_10000(self):
        self.add(10_050)
        retention.prune()
        self.assertEqual(ActivityEvent.objects.count(), 10_000)

    def test_clean_up_never_touches_the_delivery_queue(self):
        account = MonitoredAccount.objects.create(handle="a.bsky.social", first_poll_done=True)
        for rkey, state in [("p", SeenPost.PENDING), ("r", SeenPost.RETRY), ("s", SeenPost.SENDING),
                            ("x", SeenPost.SENT)]:
            poller.claim_post(account, feed_item(rkey), state)
            log.record(log.SEND_FAILED, "warning", f"about {rkey}", account=account)
        SeenPost.objects.update(seen_at=self.clock.now - timedelta(days=60))
        ActivityEvent.objects.update(created_at=self.clock.now - timedelta(days=60))
        with override_settings(ACTIVITY_MAX_EVENTS=1):
            retention.prune()
        self.assertEqual(ActivityEvent.objects.count(), 0)
        self.assertEqual(SeenPost.objects.count(), 4, "every queued post is still there")
        self.assertEqual(set(SeenPost.objects.values_list("status", flat=True)),
                         {SeenPost.PENDING, SeenPost.RETRY, SeenPost.SENDING, SeenPost.SENT})

    def test_deleting_accounts_and_channels_is_not_blocked_by_the_log(self):
        channel = DiscordWebhook.objects.create(name="#a", url=WEBHOOK)
        account = MonitoredAccount.objects.create(handle="a.bsky.social")
        log.record("post_sent", "success", "x", account=account, channel=channel)
        account.delete()
        channel.delete()
        event = ActivityEvent.objects.get()
        self.assertEqual((event.account_label, event.channel_label), ("@a.bsky.social", "#a"))

    def test_runs_after_every_check(self):
        self.add(3, age=timedelta(days=31))
        with mock.patch("bluesky_monitor.poller.poll_all_active_accounts", return_value=poller.CycleSummary()):
            scheduler.run_cycle("A")
        self.assertEqual(ActivityEvent.objects.filter(type="post_sent").count(), 0)

    def test_command(self):
        self.add(2, age=timedelta(days=31))
        out = StringIO()
        call_command("prune_activity", stdout=out)
        self.assertIn("Removed 2 entries older than 30 days", out.getvalue())


# ====================================================================== secrets
@override_settings(**CREDS)
class SecretHandlingTests(ClockMixin, APITestCase):
    def setUp(self):
        self.start_clock()
        login_ok()

    def test_scrub_patterns(self):
        cases = [
            (f"posting to {WEBHOOK} failed", TOKEN),
            (f"Authorization: Bearer {SESSION}", SESSION),
            (f'{{"accessJwt": "{SESSION}"}}', SESSION),
            ("password=hunter2hunter2", "hunter2hunter2"),
            (f"login with {PASSWORD}", PASSWORD),
            ("app_password: 'my-secret-value'", "my-secret-value"),
        ]
        for text, secret in cases:
            self.assertNotIn(secret, scrub(text), text)
        self.assertEqual(scrub("BLUESKY_PASSWORD=set"), "BLUESKY_PASSWORD=set", "status words are kept")
        self.assertEqual(scrub("plain message"), "plain message")
        self.assertNotIn("abc12345", scrub("x abc12345 y", known_secrets=["abc12345"]))

    def test_stored_events_never_contain_secrets(self):
        weird_password = "Pw-not-in-a-pattern-9"
        from botconfig import store
        store.save({"bluesky_password": weird_password}, keep_blank_password=False)
        event = log.problem(log.CHECK_FAILED, "internal",
                            f"failed with {weird_password} at {WEBHOOK}",
                            technical=f"session_string={SESSION} pw={weird_password} raw {SESSION}",
                            detail={"nested": [f"{OTHER_HOOK}", {"pw": weird_password}]})
        stored = all_text(ActivityEvent.objects.filter(pk=event.pk).values().first())
        for secret in (weird_password, TOKEN, SESSION, "OtherSecretToken"):
            self.assertNotIn(secret, stored)

    def test_discord_error_bodies_are_scrubbed(self):
        reply = mock.Mock(status_code=404, text=f'{{"message": "Unknown Webhook", "url": "{WEBHOOK}"}}')
        error = requests.HTTPError("404 Client Error for url: " + WEBHOOK, response=reply)
        with mock.patch("discord_alerts.sender.requests.post") as post:
            post.return_value.raise_for_status.side_effect = error
            result = deliver_alert(WEBHOOK, "a", "A", "t", "u")
        self.assertEqual(result.category, "discord_webhook_invalid")
        self.assertIn("Unknown Webhook", result.technical)
        self.assertNotIn(TOKEN, result.technical + result.error)

    def test_network_errors_keep_only_the_error_type(self):
        with mock.patch("discord_alerts.sender.requests.post",
                        side_effect=requests.ConnectionError(f"Max retries exceeded with url: {WEBHOOK}")):
            result = deliver_alert(WEBHOOK, "a", "A", "t", "u")
        self.assertEqual((result.category, result.technical), ("discord_unavailable", "ConnectionError"))

    def test_full_flow_leaks_nothing_into_the_log_or_the_api(self):
        channel = DiscordWebhook.objects.create(name="#art", url=WEBHOOK, is_default=True)
        account = MonitoredAccount.objects.create(handle="a.bsky.social", first_poll_done=True)
        reply = mock.Mock(status_code=500, text=f"upstream error contacting {WEBHOOK}")
        error = requests.HTTPError("500 Server Error for url: " + WEBHOOK, response=reply)
        with mock.patch("discord_alerts.sender.requests.post") as post:
            post.return_value.raise_for_status.side_effect = error
            poller.poll_account(account, session=FakeSession(feed_item("p")))
        failure = BlueskyLoginError(f"login {HANDLE} / {PASSWORD} refused, token {SESSION}")
        poller.poll_account(account, session=FakeSession(error=RuntimeError(f"bad {PASSWORD} {SESSION}")))
        log.problem(log.LOGIN_FAILED, "bluesky_login_rejected", "x", exc=failure)
        checker_running(self.clock.now)
        texts = [all_text(list(ActivityEvent.objects.values()))]
        for url in ["/api/activity/", "/api/status/"] + [f"/api/activity/{pk}/" for pk in
                                                          ActivityEvent.objects.values_list("pk", flat=True)]:
            res = self.client.get(url)
            self.assertEqual(res.status_code, 200, url)
            texts.append(res.content.decode())
        self.assertTrue(ActivityEvent.objects.filter(type=log.SEND_FAILED, channel_id=channel.pk).exists())
        for text in texts:
            for secret in (TOKEN, PASSWORD, SESSION):
                self.assertNotIn(secret, text)


# ====================================================================== migrations
class MigrationTests(TransactionTestCase):
    """The new table and columns can be removed and added again without losing data."""
    before = [("activity", None), ("bluesky_monitor", "0003_account_channel")]

    @property
    def after(self):
        # Forward again to the latest migrations (later steps add their own on top).
        return MigrationExecutor(connection).loader.graph.leaf_nodes()

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)

    def tearDown(self):
        self.migrate(self.after)

    def test_reversible_and_data_is_kept(self):
        account = MonitoredAccount.objects.create(handle="kept.bsky.social", first_poll_done=True)
        poller.claim_post(account, feed_item("q"), SeenPost.RETRY)
        log.record("post_sent", "success", "x", account=account)
        self.migrate(self.before)
        with connection.cursor() as cursor:
            tables = connection.introspection.table_names(cursor)
            columns = [c.name for c in connection.introspection.get_table_description(
                cursor, "bluesky_monitor_monitoredaccount")]
            cursor.execute("SELECT handle FROM bluesky_monitor_monitoredaccount")
            self.assertEqual(cursor.fetchall(), [("kept.bsky.social",)])
            cursor.execute("SELECT status FROM bluesky_monitor_seenpost")
            self.assertEqual(cursor.fetchall(), [("retry",)])
        self.assertNotIn("activity_activityevent", tables)
        self.assertNotIn("last_check_ok", columns)
        self.migrate(self.after)
        account = MonitoredAccount.objects.get(handle="kept.bsky.social")
        self.assertIsNone(account.last_check_ok)
        self.assertEqual(SeenPost.objects.get().status, SeenPost.RETRY)
