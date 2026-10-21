"""Step 6: profile lookup, login test, "Check all", per-account status, announce options.

Bluesky and Discord are always faked; nothing here touches the network.
"""
import json
import logging
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest import mock

from atproto_client import exceptions as at_exc
from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from activity.models import ActivityEvent
from botconfig import store
from discord_alerts import channels
from discord_alerts.models import DiscordWebhook
from discord_alerts.sender import DeliveryResult, build_payload
from . import account_status, bluesky, check_all, jobs, locks, poller, profiles, scheduler
from .bluesky import BlueskyLoginError, SharedSession, _fingerprint
from .models import BlueskySessionState, MonitoredAccount, SchedulerState, SeenPost
from .tests import FakeSession, feed_item

HANDLE = "bot.bsky.social"
PASSWORD = "abcd-efgh-ijkl-mnop"           # looks like a real App Password
OTHER_PASSWORD = "wxyz-wxyz-wxyz-wxyz"
SESSION_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJkaWQ6cGxjOmJvdCJ9.c2Vzc2lvbi1zZWNyZXQ"
WEBHOOK = "https://discord.com/api/webhooks/42/hook-token-abcdefghijklmnop"
CREDS = dict(BLUESKY_HANDLE=HANDLE, BLUESKY_PASSWORD=PASSWORD)


def _resp(error, status=400, headers=None):
    return SimpleNamespace(status_code=status, content=SimpleNamespace(error=error, message=error),
                           headers=headers or {})


def rate_limited(seconds=30):
    return at_exc.RateLimitExceededError(_resp("RateLimitExceeded", 429, {"retry-after": str(seconds)}))


def profile(handle="alice.bsky.social", did="did:plc:alice", **kw):
    values = dict(handle=handle, did=did, display_name="Alice", avatar="https://cdn.bsky.app/img/avatar/alice.jpg",
                  description="hi", followers_count=10, posts_count=5)
    values.update(kw)
    return SimpleNamespace(**values)


class FakePublicClient:
    """Stands in for the public Bluesky client in profile lookups."""
    calls = []
    answer = None  # a profile, or an exception to raise

    def get_profile(self, actor):
        FakePublicClient.calls.append(actor)
        if isinstance(FakePublicClient.answer, Exception):
            raise FakePublicClient.answer
        return FakePublicClient.answer


def all_text(data) -> str:
    return json.dumps(data, default=str)


class CacheMixin:
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)


# ============================================================================ handles
class HandleTests(TestCase):
    def test_what_users_type_or_paste_is_cleaned_up(self):
        cases = {
            "alice.bsky.social": "alice.bsky.social",
            "  @Alice.BSKY.social ": "alice.bsky.social",
            "https://bsky.app/profile/Alice.bsky.social": "alice.bsky.social",
            "bsky.app/profile/alice.bsky.social/post/3k2": "alice.bsky.social",
            "​alice.bsky.social​": "alice.bsky.social",
            "did:plc:abc123xyz": "did:plc:abc123xyz",
            "example.com": "example.com",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(profiles.normalize_handle(raw).actor, expected)

    def test_bad_input_is_explained(self):
        for raw in ("", "   ", "a b.bsky.social", "-bad.bsky.social", "name.123", "x.local",
                    "https://example.com/alice", "did:nope", "a" * 400):
            with self.subTest(raw=raw):
                norm = profiles.normalize_handle(raw)
                self.assertFalse(norm.ok)
                self.assertTrue(norm.error)

    def test_a_bare_name_gets_a_suggestion(self):
        norm = profiles.normalize_handle("Alice")
        self.assertFalse(norm.ok)
        self.assertEqual(norm.suggestion, "alice.bsky.social")


# ============================================================================ profile lookup
class ProfileLookupTests(CacheMixin, APITestCase):
    url = "/api/bluesky/profile/"

    def setUp(self):
        super().setUp()
        FakePublicClient.calls, FakePublicClient.answer = [], profile()
        patcher = mock.patch.object(profiles, "client_factory", FakePublicClient)
        patcher.start()
        self.addCleanup(patcher.stop)

    def get(self, handle):
        return self.client.get(self.url, {"handle": handle})

    def test_found_profile_has_handle_name_avatar_and_exists(self):
        res = self.get("@Alice.bsky.social")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual((body["result"], body["exists"], body["code"]), ("found", True, "found"))
        self.assertEqual(body["query"], "@Alice.bsky.social", "the query is echoed exactly, for stale answers")
        self.assertEqual(body["handle"], "alice.bsky.social")
        p = body["profile"]
        self.assertEqual((p["handle"], p["display_name"], p["did"]), ("alice.bsky.social", "Alice", "did:plc:alice"))
        self.assertEqual(p["avatar_url"], "https://cdn.bsky.app/img/avatar/alice.jpg")
        self.assertEqual(p["watch_as"], "alice.bsky.social")
        self.assertFalse(body["already_watched"])
        self.assertEqual(FakePublicClient.calls, ["alice.bsky.social"])
        self.assertEqual(res["Cache-Control"], "no-store")

    def test_unknown_handle_is_a_clear_not_found_answer(self):
        FakePublicClient.answer = at_exc.BadRequestError(_resp("InvalidRequest"))
        res = self.get("nobody.bsky.social")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual((body["result"], body["exists"], body["code"]), ("not_found", False, "handle_not_found"))
        self.assertIsNone(body["profile"])
        self.assertIn("nobody.bsky.social", body["message"])

    def test_deactivated_or_suspended_account_exists_but_is_unavailable(self):
        for error in ("AccountDeactivated", "AccountTakedown"):
            cache.clear()
            FakePublicClient.answer = at_exc.BadRequestError(_resp(error))
            body = self.get("gone.bsky.social").json()
            self.assertEqual((body["result"], body["exists"], body["code"]),
                             ("unavailable", True, "account_unavailable"), error)
            self.assertEqual(body["problem"]["category"], "account_unavailable")

    def test_invalid_handle_is_rejected_without_asking_bluesky(self):
        res = self.get("alice")
        self.assertEqual(res.status_code, 400)
        body = res.json()
        self.assertEqual((body["result"], body["code"], body["exists"]), ("invalid", "invalid_handle", None))
        self.assertEqual(body["suggestion"], "alice.bsky.social")
        self.assertEqual(body["query"], "alice")
        self.assertEqual(self.client.get(self.url).status_code, 400, "no handle at all")
        self.assertEqual(FakePublicClient.calls, [])

    def test_rate_limit_returns_429_and_pauses_further_lookups(self):
        FakePublicClient.answer = rate_limited(30)
        res = self.get("alice.bsky.social")
        self.assertEqual(res.status_code, 429)
        body = res.json()
        self.assertEqual((body["result"], body["code"], body["retryable"]), ("rate_limited", "bluesky_rate_limited", True))
        self.assertEqual(body["retry_after_seconds"], 30)
        self.assertEqual(res["Retry-After"], "30")
        FakePublicClient.answer = profile(handle="bob.bsky.social")
        self.assertEqual(self.get("bob.bsky.social").status_code, 429, "Bluesky isn't asked again while limited")
        self.assertEqual(len(FakePublicClient.calls), 1)

    def test_network_trouble_is_retryable_and_not_cached(self):
        FakePublicClient.answer = at_exc.NetworkError()
        res = self.get("alice.bsky.social")
        self.assertEqual(res.status_code, 503)
        body = res.json()
        self.assertEqual((body["result"], body["code"], body["retryable"], body["exists"]),
                         ("error", "bluesky_unavailable", True, None))
        FakePublicClient.answer = at_exc.InvokeTimeoutError()
        self.assertEqual(self.get("alice.bsky.social").status_code, 503)
        FakePublicClient.answer = profile()
        self.assertEqual(self.get("alice.bsky.social").json()["result"], "found", "a later try works at once")
        self.assertEqual(len(FakePublicClient.calls), 3)

    def test_server_errors_and_surprises(self):
        FakePublicClient.answer = at_exc.RequestException(_resp("InternalServerError", 500))
        self.assertEqual(self.get("a.bsky.social").status_code, 503)
        FakePublicClient.answer = RuntimeError("weird")
        res = self.get("b.bsky.social")
        self.assertEqual((res.status_code, res.json()["code"]), (502, "bluesky_error"))

    def test_answers_are_cached_briefly_for_search_as_you_type(self):
        self.get("alice.bsky.social")
        body = self.get("ALICE.bsky.social").json()
        self.assertTrue(body["cached"])
        self.assertEqual(body["query"], "ALICE.bsky.social")
        self.assertEqual(len(FakePublicClient.calls), 1)
        FakePublicClient.answer = at_exc.BadRequestError(_resp("InvalidRequest"))
        self.get("nobody.bsky.social")
        self.get("nobody.bsky.social")
        self.assertEqual(len(FakePublicClient.calls), 2, "not-found answers are cached too")

    def test_already_watched_is_reported_with_the_account_id(self):
        account = MonitoredAccount.objects.create(handle="alice.bsky.social")
        body = self.get("https://bsky.app/profile/alice.bsky.social").json()
        self.assertTrue(body["already_watched"])
        self.assertEqual(body["watched_account_id"], account.pk)
        self.assertIn("already watching", body["message"])
        # Watching it by its ID counts as well.
        account.handle = "did:plc:alice"
        account.save()
        self.assertTrue(self.get("alice.bsky.social").json()["already_watched"])

    def test_a_handle_that_no_longer_checks_out_is_watched_by_its_id(self):
        FakePublicClient.answer = profile(handle="handle.invalid", did="did:plc:lost")
        body = self.get("did:plc:lost").json()
        self.assertEqual(body["result"], "found")
        self.assertEqual(body["profile"]["watch_as"], "did:plc:lost")

    @override_settings(**CREDS)
    def test_lookup_never_uses_the_bots_login(self):
        store.get()
        with mock.patch.object(SharedSession, "client_factory", side_effect=AssertionError("login used")):
            self.assertEqual(self.get("alice.bsky.social").status_code, 200)
        self.assertNotIn(PASSWORD, all_text(self.get("alice.bsky.social").json()))


# ============================================================================ login test
class FakeLoginClient:
    """Stands in for atproto's Client in login tests."""
    logins = []
    error = None

    def __init__(self):
        self._session = None
        self.callbacks = []

    def on_session_change(self, cb):  # never registered by the login test
        self.callbacks.append(cb)

    def login(self, login=None, password=None, session_string=None, fetch_bsky_profile=True):
        FakeLoginClient.logins.append(login)
        if FakeLoginClient.error is not None:
            raise FakeLoginClient.error
        self._session = SimpleNamespace(did="did:plc:bot", handle=login, access_jwt=SESSION_TOKEN)

    def export_session_string(self):
        return SESSION_TOKEN


@override_settings(**CREDS, LOGIN_TEST_LIMIT=5, LOGIN_TEST_WINDOW_SECONDS=300)
class LoginTestApiTests(CacheMixin, APITestCase):
    url = "/api/settings/test-login/"

    def setUp(self):
        super().setUp()
        store.get()  # the saved login
        FakeLoginClient.logins, FakeLoginClient.error = [], None
        patcher = mock.patch.object(SharedSession, "client_factory", FakeLoginClient)
        patcher.start()
        self.addCleanup(patcher.stop)
        BlueskySessionState.objects.create(pk=1, handle=HANDLE, credential_fingerprint=_fingerprint(HANDLE, PASSWORD),
                                           session_string="saved-session")

    def post(self, **data):
        return self.client.post(self.url, data, format="json")

    def settings_unchanged(self):
        row = store.get()
        self.assertEqual((row.bluesky_handle, row.bluesky_password), (HANDLE, PASSWORD))
        state = BlueskySessionState.objects.get(pk=1)
        self.assertEqual(state.session_string, "saved-session", "the saved session is never replaced")
        self.assertEqual(state.credential_fingerprint, _fingerprint(HANDLE, PASSWORD))

    def test_new_credentials_are_tested_but_not_saved(self):
        res = self.post(bluesky_handle="@new.bsky.social", bluesky_password=OTHER_PASSWORD)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual((body["ok"], body["result"], body["code"]), (True, "ok", "login_ok"))
        self.assertEqual((body["saved"], body["matches_saved"], body["used_saved_password"]), (False, False, False))
        self.assertEqual(body["account"], {"did": "did:plc:bot", "handle": "new.bsky.social"})
        self.assertIn("isn't saved yet", body["message"])
        self.assertEqual(FakeLoginClient.logins, ["new.bsky.social"])
        self.settings_unchanged()

    def test_blank_fields_test_the_saved_login(self):
        body = self.post().json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["used_saved_handle"] and body["used_saved_password"] and body["matches_saved"])
        self.assertEqual(body["message"], "The saved login works.")
        self.settings_unchanged()

    def test_rejected_login_changes_nothing(self):
        BlueskySessionState.objects.filter(pk=1).update(login_failures=0, login_retry_after=None)
        FakeLoginClient.error = at_exc.UnauthorizedError(_resp("AuthenticationRequired", 401))
        res = self.post(bluesky_password=OTHER_PASSWORD)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual((body["ok"], body["result"], body["code"]), (False, "rejected", "bluesky_login_rejected"))
        self.assertEqual(body["problem"]["fix"], "update_login")
        state = BlueskySessionState.objects.get(pk=1)
        self.assertEqual((state.login_failures, state.login_retry_after), (0, None), "a test never starts a wait")
        self.settings_unchanged()
        self.assertFalse(ActivityEvent.objects.exists(), "failed tests aren't checker problems")

    def test_email_code_accounts_are_told_to_use_an_app_password(self):
        FakeLoginClient.error = at_exc.UnauthorizedError(_resp("AuthFactorTokenRequired", 401))
        body = self.post(bluesky_password="my-main-password").json()
        self.assertEqual((body["result"], body["code"]), ("app_password_needed", "bluesky_app_password_needed"))
        self.assertFalse(body["password_looks_like_app_password"])
        self.assertIn("App Password", body["warning"])

    def test_rate_limit_and_network_trouble(self):
        FakeLoginClient.error = rate_limited(120)
        res = self.post()
        self.assertEqual((res.status_code, res.json()["result"], res["Retry-After"]), (429, "rate_limited", "120"))
        FakeLoginClient.error = at_exc.NetworkError()
        res = self.post()
        self.assertEqual((res.status_code, res.json()["code"]), (503, "bluesky_unavailable"))
        FakeLoginClient.error = at_exc.RequestException(_resp("Oops", 500))
        self.assertEqual(self.post().status_code, 503)
        FakeLoginClient.error = RuntimeError("weird")
        self.assertEqual(self.post().status_code, 502)

    def test_no_test_while_bluesky_is_limiting_the_bots_logins(self):
        BlueskySessionState.objects.filter(pk=1).update(login_retry_after=timezone.now() + timedelta(minutes=10))
        res = self.post()
        self.assertEqual((res.status_code, res.json()["result"]), (429, "rate_limited"))
        self.assertEqual(FakeLoginClient.logins, [], "Bluesky isn't asked again")

    def test_repeated_tests_are_limited(self):
        for _ in range(5):
            self.assertEqual(self.post().status_code, 200)
        res = self.post()
        self.assertEqual((res.status_code, res.json()["code"]), (429, "too_many_tests"))
        self.assertTrue(res["Retry-After"])
        self.assertEqual(len(FakeLoginClient.logins), 5)

    def test_nothing_to_test(self):
        with mock.patch.object(store, "credentials", return_value=("", "")):
            res = self.post()
        self.assertEqual((res.status_code, res.json()["code"]), (400, "bluesky_not_configured"))
        self.assertEqual(set(res.json()["fields"]), {"bluesky_handle", "bluesky_password"})
        res = self.post(bluesky_handle="has space.bsky.social", bluesky_password="x")
        self.assertEqual((res.status_code, res.json()["code"]), (400, "invalid_login_test"))
        res = self.client.post(self.url, {"bluesky_password": ["a"]}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(FakeLoginClient.logins, [])

    def test_a_working_saved_login_ends_the_wait_after_failures(self):
        BlueskySessionState.objects.filter(pk=1).update(
            login_failures=2, login_retry_after=timezone.now() + timedelta(minutes=40), last_error="rejected",
            session_string="")
        from activity import status as bot_status
        self.assertEqual(bot_status.bluesky_login(timezone.now())["state"], "failed")
        self.assertTrue(self.post().json()["ok"])
        state = BlueskySessionState.objects.get(pk=1)
        self.assertEqual((state.login_failures, state.login_retry_after, state.last_error), (0, None, ""))
        self.assertEqual(bot_status.bluesky_login(timezone.now())["state"], "unknown", "tried again at next check")
        self.assertTrue(ActivityEvent.objects.filter(type="login_recovered").exists())

    def test_testing_other_credentials_leaves_the_wait_alone(self):
        later = timezone.now() + timedelta(minutes=40)
        BlueskySessionState.objects.filter(pk=1).update(login_failures=2, login_retry_after=later)
        self.assertTrue(self.post(bluesky_password=OTHER_PASSWORD).json()["ok"])
        self.assertEqual(BlueskySessionState.objects.get(pk=1).login_failures, 2)

    def test_passwords_and_session_tokens_never_appear(self):
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        root.addHandler(handler)
        old_level = root.level
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(root.setLevel, old_level)
        texts = []
        for error in (None, at_exc.UnauthorizedError(_resp("AuthenticationRequired", 401)), at_exc.NetworkError(),
                      RuntimeError(f"boom {OTHER_PASSWORD} {SESSION_TOKEN}")):
            cache.clear()
            FakeLoginClient.error = error
            texts.append(self.post(bluesky_password=OTHER_PASSWORD).content.decode())
            texts.append(self.post().content.decode())
        texts.append(self.client.get("/api/settings/").content.decode())
        texts.append(stream.getvalue())
        texts.append(all_text(list(ActivityEvent.objects.values())))
        for secret in (PASSWORD, OTHER_PASSWORD, SESSION_TOKEN, "saved-session"):
            self.assertFalse(any(secret in t for t in texts), "a secret leaked")

    def test_settings_show_the_login_status(self):
        body = self.client.get("/api/settings/").json()
        self.assertEqual(body["bluesky_login"]["state"], "ok")
        self.assertNotIn("bluesky_password", body)


# ============================================================================ per-account status
class AccountStatusTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.account = MonitoredAccount.objects.create(handle="a.bsky.social")

    def state(self):
        self.account.refresh_from_db()
        return account_status.describe(self.account, self.now)

    def test_new_account_waits_for_its_first_check(self):
        info = self.state()
        self.assertEqual(info["state"], "waiting_first_check")
        self.assertEqual(info["first_check"], {"done": False, "at": None, "seeded_posts": None})
        self.assertIsNone(info["last_error"])

    def test_checking_paused_error_and_active(self):
        MonitoredAccount.objects.filter(pk=self.account.pk).update(check_lock_until=self.now + timedelta(minutes=1))
        self.assertEqual(self.state()["state"], "checking")
        self.assertTrue(self.state()["checking_now"])
        MonitoredAccount.objects.filter(pk=self.account.pk).update(check_lock_until=self.now - timedelta(seconds=1))
        self.assertEqual(self.state()["state"], "waiting_first_check", "an expired lock isn't a running check")

        MonitoredAccount.objects.filter(pk=self.account.pk).update(
            first_poll_done=True, last_check_ok=False, last_check_category="bluesky_unavailable",
            last_error_at=self.now, last_error_category="bluesky_unavailable")
        info = self.state()
        self.assertEqual(info["state"], "error")
        self.assertEqual((info["problem"]["category"], info["problem"]["severity"]), ("bluesky_unavailable", "warning"))
        self.assertTrue(info["last_error"]["ongoing"])

        MonitoredAccount.objects.filter(pk=self.account.pk).update(last_check_category="account_not_found")
        self.assertEqual(self.state()["problem"]["severity"], "error")

        MonitoredAccount.objects.filter(pk=self.account.pk).update(is_active=False)
        self.assertEqual(self.state()["state"], "paused")

        MonitoredAccount.objects.filter(pk=self.account.pk).update(is_active=True, last_check_ok=True,
                                                                    last_success_at=self.now)
        info = self.state()
        self.assertEqual(info["state"], "active")
        self.assertFalse(info["last_error"]["ongoing"], "a recent error stays visible after recovery")
        self.assertEqual(info["last_success_at"], self.now.isoformat())

    def test_checks_record_first_check_success_and_errors(self):
        with mock.patch("discord_alerts.sender.deliver_alert", return_value=DeliveryResult(True)):
            poller.poll_account(self.account, session=FakeSession(feed_item("b"), feed_item("a")))
            info = self.state()
            self.assertEqual(info["state"], "active")
            self.assertTrue(info["first_check"]["done"])
            self.assertEqual(info["first_check"]["seeded_posts"], 2)
            self.assertTrue(info["first_check"]["at"])
            success = info["last_success_at"]
            self.assertTrue(success)

            poller.poll_account(self.account, session=FakeSession(error=at_exc.BadRequestError(_resp("InvalidRequest"))))
            info = self.state()
            self.assertEqual((info["state"], info["problem"]["category"]), ("error", "account_not_found"))
            self.assertEqual(info["last_success_at"], success, "the last success is kept")

            poller.poll_account(self.account, session=FakeSession(feed_item("a")))
            info = self.state()
            self.assertEqual(info["state"], "active")
            self.assertEqual(info["last_error"]["category"], "account_not_found")
            self.assertFalse(info["last_error"]["ongoing"])

    def test_deactivated_feed_is_its_own_temporary_problem(self):
        poller.poll_account(self.account, session=FakeSession(error=at_exc.BadRequestError(_resp("AccountDeactivated"))))
        info = self.state()
        self.assertEqual((info["problem"]["category"], info["problem"]["severity"]), ("account_unavailable", "warning"))


class AccountApiStep6Tests(APITestCase):
    url = "/api/accounts/"

    def test_list_shows_status_and_announce_options(self):
        MonitoredAccount.objects.create(handle="a.bsky.social")
        row = self.client.get(self.url).json()["results"][0]
        for key in ("state", "state_label", "state_message", "checking_now", "problem", "first_check",
                    "last_checked_at", "last_success_at", "last_error"):
            self.assertIn(key, row)
        self.assertEqual(row["state"], "waiting_first_check")
        self.assertEqual((row["announce_posts"], row["announce_reposts"], row["announce_replies"]), (True, True, False))
        self.assertNotIn("seen_posts", row, "removed in step 10; GET /api/accounts/{id}/posts/")

    def test_accounts_from_older_code_get_the_defaults(self):
        account = MonitoredAccount.objects.create(handle="old.bsky.social")
        MonitoredAccount.objects.filter(pk=account.pk).update(announce_posts=None, announce_reposts=None,
                                                               announce_replies=None)
        row = self.client.get(f"{self.url}{account.pk}/").json()
        self.assertEqual((row["announce_posts"], row["announce_reposts"], row["announce_replies"]), (True, True, False))

    def test_announce_options_can_be_set_and_changed(self):
        res = self.client.post(self.url, {"handle": "a.bsky.social", "announce_replies": True,
                                          "announce_reposts": False}, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        account = MonitoredAccount.objects.get()
        self.assertEqual((account.announce_posts, account.announce_reposts, account.announce_replies),
                         (True, False, True))
        res = self.client.patch(f"{self.url}{account.pk}/", {"announce_posts": False}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["announce_posts"])

    def test_handles_are_cleaned_up_and_duplicates_refused(self):
        res = self.client.post(self.url, {"handle": " @Artist.BSKY.social "}, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["handle"], "artist.bsky.social")
        res = self.client.post(self.url, {"handle": "https://bsky.app/profile/artist.bsky.social"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("already watching", res.json()["handle"][0])
        res = self.client.post(self.url, {"handle": "artist"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("artist.bsky.social", res.json()["handle"][0])
        account = MonitoredAccount.objects.get()
        res = self.client.put(f"{self.url}{account.pk}/", {"handle": "@artist.bsky.social", "is_active": False},
                              format="json")
        self.assertEqual(res.status_code, 200, "an account may keep its own handle")

    def test_check_all_route_is_not_taken_for_an_account_id(self):
        self.assertEqual(self.client.get(f"{self.url}check_all/").status_code, 405)
        self.assertEqual(self.client.get(f"{self.url}abc/").status_code, 404)

    @override_settings(**CREDS)
    def test_poll_now_adds_state_first_check_and_check_result(self):
        account = MonitoredAccount.objects.create(handle="a.bsky.social")
        with mock.patch("bluesky_monitor.poller._new_session", return_value=FakeSession(feed_item("x"))):
            body = self.client.post(f"{self.url}{account.pk}/poll_now/").json()
        self.assertEqual((body["new_posts_announced"], body["was_first_poll"]), (0, True))
        self.assertEqual(body["state"], "active")
        self.assertEqual(body["first_check"]["seeded_posts"], 1)
        self.assertEqual(body["check"]["result"], "checked")
        self.assertIn("First check done", body["check"]["message"])


# ============================================================================ announce options
@override_settings(**CREDS)
class AnnounceOptionTests(TestCase):
    def setUp(self):
        channel = DiscordWebhook.objects.create(name="#general", url=WEBHOOK)
        channels.make_default(channel)
        self.account = MonitoredAccount.objects.create(handle="artist.bsky.social", first_poll_done=True)
        patcher = mock.patch("discord_alerts.sender.deliver_alert", return_value=DeliveryResult(True))
        self.send = patcher.start()
        self.addCleanup(patcher.stop)

    def items(self):
        repost = feed_item("rp", author="someone.bsky.social")
        repost.reason = SimpleNamespace(py_type="app.bsky.feed.defs#reasonRepost")
        reply = feed_item("re")
        reply.post.record.reply = SimpleNamespace(parent="x")
        pinned = feed_item("pin")
        pinned.reason = SimpleNamespace(py_type="app.bsky.feed.defs#reasonPin")
        return [feed_item("p"), repost, reply, pinned]

    def kinds(self):
        return dict(SeenPost.objects.values_list("post_uri", "kind"))

    def test_kinds_are_recognised(self):
        kinds = [poller.item_kind(i) for i in self.items()]
        self.assertEqual(kinds, ["post", "repost", "reply", "post"])

    def test_defaults_announce_posts_and_reposts_but_not_replies(self):
        poller.poll_account(self.account, session=FakeSession(*self.items()))
        report = self.account.check_report
        self.assertEqual((report.new_posts, report.posts_sent, report.turned_off), (4, 3, 1))
        statuses = dict(SeenPost.objects.values_list("kind", "status").order_by("kind"))
        self.assertEqual(statuses["reply"], SeenPost.SKIPPED)
        kinds_sent = [c.kwargs.get("kind", "post") for c in self.send.call_args_list]
        self.assertEqual(sorted(kinds_sent), ["post", "post", "repost"])

    def test_switched_off_kinds_are_noted_and_never_sent_later(self):
        MonitoredAccount.objects.filter(pk=self.account.pk).update(announce_reposts=False, announce_posts=False)
        poller.poll_account(self.account, session=FakeSession(*self.items()))
        self.send.assert_not_called()
        MonitoredAccount.objects.filter(pk=self.account.pk).update(announce_reposts=True, announce_posts=True)
        poller.poll_account(self.account, session=FakeSession(*self.items()))
        self.send.assert_not_called()  # switching on affects only posts noticed afterwards
        self.assertEqual(SeenPost.objects.filter(status=SeenPost.SKIPPED).count(), 4)

    def test_repost_and_reply_messages_say_what_they_are(self):
        post = build_payload("artist.bsky.social", "Artist", "hi", "https://bsky.app/profile/artist.bsky.social/post/1")
        self.assertEqual(post["embeds"][0]["title"], "New post from Artist")
        self.assertEqual(post["embeds"][0]["footer"]["text"], "@artist.bsky.social on Bluesky")
        repost = build_payload("artist.bsky.social", "Artist", "hi",
                               "https://bsky.app/profile/someone.bsky.social/post/1", kind="repost")
        self.assertEqual(repost["embeds"][0]["title"], "Artist reposted")
        self.assertIn("original post by @someone.bsky.social", repost["embeds"][0]["footer"]["text"])
        reply = build_payload("artist.bsky.social", "Artist", "hi", "https://bsky.app/x", kind="reply")
        self.assertEqual(reply["embeds"][0]["title"], "New reply from Artist")


# ============================================================================ check all
@override_settings(**CREDS)
class CheckAllTests(CacheMixin, APITestCase):
    url = "/api/accounts/check_all/"

    def setUp(self):
        super().setUp()
        channel = DiscordWebhook.objects.create(name="#general", url=WEBHOOK)
        channels.make_default(channel)
        self.ok = MonitoredAccount.objects.create(handle="ok.bsky.social", first_poll_done=True)
        self.broken = MonitoredAccount.objects.create(handle="broken.bsky.social", first_poll_done=True)
        self.crashy = MonitoredAccount.objects.create(handle="crashy.bsky.social", first_poll_done=True)
        self.paused = MonitoredAccount.objects.create(handle="paused.bsky.social", is_active=False)
        self.new = MonitoredAccount.objects.create(handle="new.bsky.social")
        patcher = mock.patch("discord_alerts.sender.deliver_alert", return_value=DeliveryResult(True))
        self.send = patcher.start()
        self.addCleanup(patcher.stop)
        self.session = FeedsByAccount({
            "ok.bsky.social": [feed_item("o2"), feed_item("o1")],
            "broken.bsky.social": at_exc.BadRequestError(_resp("InvalidRequest")),
            "crashy.bsky.social": [feed_item("c1")],
            "new.bsky.social": [feed_item("n2"), feed_item("n1")],
        })
        patcher = mock.patch("bluesky_monitor.poller._new_session", return_value=self.session)
        patcher.start()
        self.addCleanup(patcher.stop)

    def rows(self, body):
        return {r["handle"]: r for r in body["accounts"]}

    def check_all(self):
        """Since step 7 Check All runs in the background: queue it, let the checker run it, read the job."""
        res = self.client.post(self.url)
        self.assertEqual(res.status_code, 202)
        self.assertTrue(locks.acquire_scheduler_lease("T"))
        jobs.run_pending("T")
        job = self.client.get(res["Location"])
        self.assertEqual((job.status_code, job.json()["status"]), (200, "finished"))
        self.last_response = job
        return job.json()

    def test_every_account_gets_a_result_and_one_failure_stops_nothing(self):
        real_claim = poller.claim_post

        def claim(account, item, status, kind=None):
            if account.handle == "crashy.bsky.social":
                raise RuntimeError(f"bug while saving {PASSWORD}")
            return real_claim(account, item, status, kind=kind)

        with mock.patch("bluesky_monitor.poller.claim_post", side_effect=claim):
            body = self.check_all()
        res = self.last_response
        rows = self.rows(body)
        self.assertEqual(list(rows), ["ok.bsky.social", "broken.bsky.social", "crashy.bsky.social",
                                      "paused.bsky.social", "new.bsky.social"], "every account, in order")
        self.assertEqual((rows["ok.bsky.social"]["result"], rows["ok.bsky.social"]["posts_sent"]), ("checked", 2))
        self.assertEqual((rows["broken.bsky.social"]["result"], rows["broken.bsky.social"]["problem"]["category"]),
                         ("failed", "account_not_found"))
        self.assertEqual((rows["crashy.bsky.social"]["result"], rows["crashy.bsky.social"]["problem"]["category"]),
                         ("failed", "internal"))
        self.assertEqual((rows["paused.bsky.social"]["result"], rows["paused.bsky.social"]["reason"]),
                         ("skipped", "paused"))
        new = rows["new.bsky.social"]
        self.assertEqual((new["result"], new["first_check"], new["posts_sent"], new["state"]),
                         ("checked", True, 0, "active"))
        self.assertIn("none were sent", new["message"])
        self.assertEqual(body["result"], "partial")
        self.assertEqual(body["counts"], {"checked": 2, "failed": 2, "skipped": 1, "already_checking": 0,
                                          "new_posts": 2, "posts_sent": 2, "waiting": 0})
        self.assertEqual(self.send.call_count, 2)
        self.assertNotIn(PASSWORD, res.content.decode())
        self.assertNotIn(PASSWORD, all_text(list(ActivityEvent.objects.values())))
        crashy = MonitoredAccount.objects.get(pk=self.crashy.pk)
        self.assertEqual((crashy.last_check_ok, crashy.last_check_category), (False, "internal"))
        self.assertIsNone(crashy.check_lock_until, "the lock is released after a crash")

    def test_an_account_being_checked_elsewhere_is_reported_not_rechecked(self):
        token = locks.acquire_account_lock(self.ok.pk)
        body = self.check_all()
        row = self.rows(body)["ok.bsky.social"]
        self.assertEqual(row["result"], "already_checking")
        self.assertEqual(row["state"], "checking")
        self.assertEqual(body["counts"]["already_checking"], 1)
        self.assertNotIn("ok.bsky.social", self.session.read)
        locks.release_account_lock(self.ok.pk, token)

    def test_running_twice_never_sends_twice(self):
        self.check_all()
        sent_first_time = self.send.call_count
        self.assertEqual(sent_first_time, 3)  # o1, o2, c1; the new account's posts are only noted
        body = self.check_all()
        self.assertEqual(self.send.call_count, sent_first_time)
        self.assertEqual(self.rows(body)["ok.bsky.social"]["message"], "No new posts.")

    def test_rejected_login_skips_everything_with_the_reason(self):
        self.session.login_error = BlueskyLoginError("no")
        body = self.check_all()
        self.assertEqual(body["result"], "login_failed")
        self.assertEqual(body["problem"]["category"], "bluesky_login_rejected")
        reasons = {r["handle"]: r["reason"] for r in body["accounts"]}
        self.assertEqual(reasons["ok.bsky.social"], "login_failed")
        self.assertEqual(reasons["paused.bsky.social"], "paused")

    def test_missing_login_is_not_configured(self):
        self.session.login_error = bluesky.BlueskyNotConfigured()
        body = self.check_all()
        self.assertEqual((body["result"], body["problem"]["category"]), ("not_configured", "bluesky_not_configured"))

    def test_login_failing_part_way_is_partial(self):
        self.session.login_error_after = 1
        self.session.login_error = BlueskyLoginError("no")
        body = self.check_all()
        self.assertEqual(body["result"], "partial")
        self.assertIn("Then the check stopped", body["message"])

    def test_nothing_to_check(self):
        MonitoredAccount.objects.update(is_active=False)
        self.assertEqual(self.check_all()["result"], "nothing_to_check")
        MonitoredAccount.objects.all().delete()
        body = self.check_all()
        self.assertEqual((body["result"], body["accounts"]), ("nothing_to_check", []))

    def test_time_limit_leaves_the_rest_for_the_next_check(self):
        body = check_all.run(time_limit_seconds=-1)
        reasons = {r["handle"]: r["reason"] for r in body["accounts"]}
        self.assertEqual(reasons["ok.bsky.social"], "time_limit")
        self.assertIn("not reached", body["message"])

    def test_scheduled_checks_also_survive_one_accounts_crash(self):
        with mock.patch("bluesky_monitor.poller.claim_post", side_effect=RuntimeError("bug")):
            scheduler.run_cycle("A")
        summary = SchedulerState.objects.get(pk=1).last_cycle_summary
        self.assertEqual(summary["accounts_checked"], 4, "every active account was tried")
        self.assertEqual(summary["category"], "internal")
        self.assertFalse(ActivityEvent.objects.filter(type="cycle_failed").exists())


class FeedsByAccount:
    """A fake shared session with a feed (or an error) per account."""

    def __init__(self, feeds):
        self.feeds, self.read = feeds, []
        self.login_error, self.login_error_after = None, 0

    def author_feed(self, actor, cursor=None, limit=30):
        if self.login_error is not None and len(self.read) >= self.login_error_after:
            raise self.login_error
        self.read.append(actor)
        feed = self.feeds.get(actor, [])
        if isinstance(feed, Exception):
            raise feed
        return SimpleNamespace(feed=list(feed), cursor=None)


# ============================================================================ migration
class Step6MigrationTests(TransactionTestCase):
    before = [("bluesky_monitor", "0004_check_health")]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)

    def tearDown(self):
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())

    def test_reversible_backfilled_and_data_is_kept(self):
        account = MonitoredAccount.objects.create(handle="kept.bsky.social", first_poll_done=True)
        failing = MonitoredAccount.objects.create(handle="failing.bsky.social")
        poller.claim_post(account, feed_item("q"), SeenPost.RETRY)
        self.migrate(self.before)
        with connection.cursor() as cursor:
            columns = [c.name for c in connection.introspection.get_table_description(
                cursor, "bluesky_monitor_monitoredaccount")]
            self.assertNotIn("announce_posts", columns)
            now = timezone.now().isoformat()
            cursor.execute("UPDATE bluesky_monitor_monitoredaccount SET last_check_ok=1, last_checked_at=%s "
                           "WHERE id=%s", [now, account.pk])
            cursor.execute("UPDATE bluesky_monitor_monitoredaccount SET last_check_ok=0, last_checked_at=%s, "
                           "last_check_category='account_not_found' WHERE id=%s", [now, failing.pk])
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        account = MonitoredAccount.objects.get(pk=account.pk)
        self.assertEqual((account.announce_posts, account.announce_reposts, account.announce_replies),
                         (True, True, False))
        self.assertIsNotNone(account.last_success_at)
        failing = MonitoredAccount.objects.get(pk=failing.pk)
        self.assertEqual(failing.last_error_category, "account_not_found")
        self.assertIsNotNone(failing.last_error_at)
        post = SeenPost.objects.get()
        self.assertEqual((post.status, post.kind), (SeenPost.RETRY, None))
