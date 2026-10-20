"""The shared Bluesky session: reuse, refresh, expiry, wrong password, network trouble."""
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from atproto_client import exceptions as at_exc
from atproto_client.client.session import SessionEvent
from django.test import TestCase, override_settings
from django.utils import timezone

from . import bluesky, poller
from .bluesky import BlueskyLoginError, BlueskyUnavailable, SharedSession
from .models import BlueskySessionState, MonitoredAccount


def _resp(error, status=400):
    return SimpleNamespace(status_code=status, content=SimpleNamespace(error=error, message=error))


EXPIRED = at_exc.BadRequestError(_resp("ExpiredToken"))
BAD_PASSWORD = at_exc.UnauthorizedError(_resp("AuthenticationRequired", 401))
RATE_LIMITED = at_exc.RateLimitExceededError(_resp("RateLimitExceeded", 429))
NETWORK = at_exc.NetworkError()


class FakeClient:
    """Records logins. Behaviour per instance is driven by class-level queues."""
    instances = []
    login_errors = []   # errors raised by password logins, in order
    feed_errors = []    # errors raised by get_author_feed, in order

    def __init__(self):
        self.callbacks, self.logins, self.session = [], [], None
        FakeClient.instances.append(self)

    def on_session_change(self, cb):
        self.callbacks.append(cb)

    def login(self, login=None, password=None, session_string=None, fetch_bsky_profile=True):
        self.logins.append("session" if session_string else "password")
        if session_string:
            self.session = session_string
            event = SessionEvent.IMPORT
        else:
            if FakeClient.login_errors:
                raise FakeClient.login_errors.pop(0)
            self.session = f"fresh-{len(FakeClient.instances)}"
            event = SessionEvent.CREATE
        for cb in self.callbacks:
            cb(event, None)

    def refresh(self):  # what atproto does on its own when the access token expires
        self.session = self.session + "-refreshed"
        for cb in self.callbacks:
            cb(SessionEvent.REFRESH, None)

    def export_session_string(self):
        return self.session

    def get_author_feed(self, actor, cursor=None, limit=30):
        if FakeClient.feed_errors:
            raise FakeClient.feed_errors.pop(0)
        return SimpleNamespace(feed=[], cursor=None)


@override_settings(BLUESKY_HANDLE="bot.bsky.social", BLUESKY_PASSWORD="app-pw")
class SharedSessionTests(TestCase):
    def setUp(self):
        FakeClient.instances, FakeClient.login_errors, FakeClient.feed_errors = [], [], []
        p = mock.patch.object(SharedSession, "client_factory", FakeClient)
        p.start()
        self.addCleanup(p.stop)

    def password_logins(self):
        return sum(c.logins.count("password") for c in FakeClient.instances)

    def saved(self):
        return BlueskySessionState.objects.get(pk=1)

    def test_first_use_logs_in_once_and_saves_the_session(self):
        SharedSession().author_feed("a")
        self.assertEqual(self.password_logins(), 1)
        self.assertTrue(self.saved().session_string.startswith("fresh-"))

    def test_saved_session_is_reused_without_a_password_login(self):
        SharedSession().author_feed("a")
        SharedSession().author_feed("b")  # e.g. the next cycle, or another process
        self.assertEqual(self.password_logins(), 1)
        self.assertEqual(FakeClient.instances[-1].logins, ["session"])

    def test_one_login_per_cycle_for_many_accounts(self):
        """Fixes the old behaviour of one login per account per check."""
        for h in ("a", "b", "c", "d", "e"):
            MonitoredAccount.objects.create(handle=f"{h}.bsky.social", first_poll_done=True)
        with mock.patch("bluesky_monitor.poller._new_session", side_effect=SharedSession):
            poller.poll_all_active_accounts()
        self.assertEqual(self.password_logins(), 1)

    def test_refreshed_tokens_are_saved_for_other_processes(self):
        s = SharedSession()
        s.author_feed("a")
        s._client.refresh()
        self.assertTrue(self.saved().session_string.endswith("-refreshed"))

    def test_expired_session_logs_in_again_once_and_retries_the_call(self):
        SharedSession().author_feed("a")
        FakeClient.feed_errors = [EXPIRED]
        SharedSession().author_feed("b")
        self.assertEqual(self.password_logins(), 2)

    def test_newer_session_saved_by_another_process_is_tried_before_a_password_login(self):
        SharedSession().author_feed("a")
        s = SharedSession()
        s.author_feed("b")
        BlueskySessionState.objects.filter(pk=1).update(session_string="saved-by-other-process")
        FakeClient.feed_errors = [EXPIRED]
        s.author_feed("c")
        self.assertEqual(self.password_logins(), 1)
        self.assertEqual(s._client.session, "saved-by-other-process")

    def test_still_rejected_after_logging_in_again_raises_login_error(self):
        FakeClient.feed_errors = [EXPIRED, EXPIRED]
        with self.assertRaises(BlueskyLoginError):
            SharedSession().author_feed("a")

    def test_wrong_password_backs_off_instead_of_retrying(self):
        FakeClient.login_errors = [BAD_PASSWORD]
        with self.assertRaises(BlueskyLoginError):
            SharedSession().author_feed("a")
        state = self.saved()
        self.assertEqual(state.login_failures, 1)
        self.assertGreater(state.login_retry_after, timezone.now() + timedelta(minutes=9))

        with self.assertRaises(BlueskyLoginError):
            SharedSession().author_feed("a")
        self.assertEqual(self.password_logins(), 1, "no new login attempt during the back-off")

    def test_back_off_ends_and_login_is_tried_again(self):
        FakeClient.login_errors = [BAD_PASSWORD]
        with self.assertRaises(BlueskyLoginError):
            SharedSession().author_feed("a")
        BlueskySessionState.objects.filter(pk=1).update(login_retry_after=timezone.now() - timedelta(seconds=1))
        SharedSession().author_feed("a")
        self.assertEqual(self.password_logins(), 2)
        self.assertEqual(self.saved().login_failures, 0)

    def test_changing_the_password_skips_the_back_off(self):
        FakeClient.login_errors = [BAD_PASSWORD]
        with self.assertRaises(BlueskyLoginError):
            SharedSession().author_feed("a")
        with override_settings(BLUESKY_PASSWORD="new-app-pw"):
            SharedSession().author_feed("a")
        self.assertEqual(self.password_logins(), 2)

    def test_saved_session_is_not_used_for_different_credentials(self):
        SharedSession().author_feed("a")
        with override_settings(BLUESKY_HANDLE="other.bsky.social"):
            SharedSession().author_feed("a")
        self.assertEqual(self.password_logins(), 2)

    def test_network_failure_is_temporary_and_keeps_the_session(self):
        SharedSession().author_feed("a")
        before = self.saved().session_string
        FakeClient.feed_errors = [NETWORK]
        with self.assertRaises(BlueskyUnavailable):
            SharedSession().author_feed("b")
        self.assertEqual(self.saved().session_string, before)
        self.assertIsNone(self.saved().login_retry_after)

    def test_rate_limited_login_is_temporary(self):
        FakeClient.login_errors = [RATE_LIMITED]
        with self.assertRaises(BlueskyUnavailable):
            SharedSession().author_feed("a")

    @override_settings(BLUESKY_PASSWORD="")
    def test_missing_credentials(self):
        with self.assertRaises(BlueskyLoginError):
            SharedSession().author_feed("a")

    def test_login_error_stops_the_cycle(self):
        for h in ("a", "b"):
            MonitoredAccount.objects.create(handle=f"{h}.bsky.social", first_poll_done=True)
        FakeClient.login_errors = [BAD_PASSWORD]
        with mock.patch("bluesky_monitor.poller._new_session", side_effect=SharedSession):
            poller.poll_all_active_accounts()
        self.assertEqual(self.password_logins(), 1, "the second account doesn't try again")
