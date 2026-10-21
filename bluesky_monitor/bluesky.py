"""One shared Bluesky login for the whole bot.

Logging in (createSession) is rate-limited by Bluesky, so the bot logs in once, saves
the session in the database, and every process reuses it. Recovery rules:

- Expired access token: the atproto client refreshes it by itself; the new session is saved.
- Expired or revoked session: log in again with the password, once, then retry the call.
- Password rejected: stop and back off (10 min, doubling to 1 h), so a wrong password
  never hammers Bluesky. Changing the credentials skips the back-off.
- Network trouble or rate limit: raise BlueskyUnavailable; the next check tries again.
"""
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import timedelta

from atproto import Client
from atproto_client import exceptions as at_exc
from atproto_client.client.session import SessionEvent
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from .models import BlueskySessionState

logger = logging.getLogger(__name__)

AUTH_ERROR_NAMES = {"ExpiredToken", "InvalidToken", "AuthenticationRequired", "AuthMissing", "AccountTakedown"}
LOGIN_BACKOFF = [timedelta(minutes=10), timedelta(minutes=20), timedelta(minutes=40), timedelta(hours=1)]
RATE_LIMIT_BACKOFF = timedelta(minutes=15)


class BlueskyLoginError(Exception):
    """The bot's own Bluesky login is missing or was rejected. Checking can't continue.

    `category` is the activity.problems code that explains it to the user.
    """

    def __init__(self, message="", category="bluesky_login_rejected"):
        super().__init__(message)
        self.category = category


class BlueskyNotConfigured(BlueskyLoginError):
    """No Bluesky handle or App Password has been saved yet."""

    def __init__(self, message="The bot's Bluesky login isn't set up yet."):
        super().__init__(message, category="bluesky_not_configured")


class BlueskyUnavailable(Exception):
    """Bluesky couldn't be reached right now (network, timeout, rate limit). Try later."""

    def __init__(self, message="", category="bluesky_unavailable"):
        super().__init__(message)
        self.category = category


def _log_login(event_type, category=""):
    from activity import log

    if event_type == log.LOGIN_FAILED:
        log.problem(log.LOGIN_FAILED, category, "Bluesky didn't accept the bot's login. Checks are paused "
                    "until it works.", merge_repeats=True)
    else:
        log.record(log.LOGIN_RECOVERED, "success", "The bot's Bluesky login works again.")


def is_auth_error(exc: Exception) -> bool:
    if isinstance(exc, (at_exc.UnauthorizedError, at_exc.LoginRequiredError)):
        return True
    if isinstance(exc, at_exc.BadRequestError):
        content = getattr(getattr(exc, "response", None), "content", None)
        return getattr(content, "error", None) in AUTH_ERROR_NAMES
    return False


def _fingerprint(handle: str, password: str) -> str:
    return hashlib.sha256(f"{handle}\0{password}".encode()).hexdigest()


def _state() -> BlueskySessionState:
    state = BlueskySessionState.objects.filter(pk=1).first()
    if state is None:
        try:
            with transaction.atomic():
                state = BlueskySessionState.objects.create(pk=1)
        except IntegrityError:
            state = BlueskySessionState.objects.get(pk=1)
    return state


def _credentials():
    from botconfig import store

    handle, password = store.credentials()
    if not handle or not password:
        raise BlueskyNotConfigured()
    return handle, password


class SharedSession:
    """A Bluesky client backed by the shared, saved session. Create one per check cycle."""

    client_factory = Client  # replaced in tests

    def __init__(self):
        self._client = None
        self._password_login_done = False

    # -- public -----------------------------------------------------------------
    def author_feed(self, actor: str, cursor=None, limit: int = 30):
        return self._call(lambda c: c.get_author_feed(actor=actor, cursor=cursor, limit=limit))

    # -- internals --------------------------------------------------------------
    def _call(self, fn):
        """Run fn(client). On an auth error, reconnect and retry: first with a newer session
        another process may have saved, then with one password login. Never more than that."""
        if self._client is None:
            self._client = self._connect()
        while True:
            try:
                return fn(self._client)
            except Exception as exc:
                if not is_auth_error(exc):
                    if isinstance(exc, at_exc.RateLimitExceededError):
                        raise BlueskyUnavailable("Bluesky is limiting requests right now.",
                                                 category="bluesky_rate_limited") from exc
                    if isinstance(exc, (at_exc.NetworkError, at_exc.RequestException)):
                        raise BlueskyUnavailable(f"Bluesky isn't responding properly: {exc}") from exc
                    raise
                if self._password_login_done:
                    raise BlueskyLoginError("Bluesky rejected the bot's login.") from exc
                logger.info("Bluesky session no longer valid; reconnecting")
                self._client = self._connect(stale_session=self._client_session_string())

    def _client_session_string(self):
        try:
            return self._client.export_session_string()
        except Exception:
            return None

    def _new_client(self, fingerprint, handle):
        client = self.client_factory()

        def save(event, _session):
            # Called by atproto on login and on every token refresh.
            if event == SessionEvent.IMPORT:
                return  # loading the saved session; nothing new to store
            BlueskySessionState.objects.filter(pk=1).update(
                handle=handle, credential_fingerprint=fingerprint,
                session_string=client.export_session_string(), updated_at=timezone.now(),
            )

        client.on_session_change(save)
        return client

    def _connect(self, stale_session: str | None = None):
        handle, password = _credentials()
        fp = _fingerprint(handle, password)
        state = _state()
        same_creds = state.credential_fingerprint == fp

        # Reuse the saved session, unless it is the one that just failed.
        # (Another process may already have saved a fresh one; that is tried first.)
        if same_creds and state.session_string and state.session_string != stale_session:
            client = self._new_client(fp, handle)
            client.login(session_string=state.session_string, fetch_bsky_profile=False)  # no network call
            return client

        now = timezone.now()
        if same_creds and state.login_retry_after and now < state.login_retry_after:
            # Waiting after a rejected password, or after Bluesky limited logins.
            category = "bluesky_login_rejected" if state.login_failures else "bluesky_rate_limited"
            raise BlueskyLoginError(state.last_error or "Bluesky rejected the bot's login.", category=category)

        client = self._new_client(fp, handle)
        try:
            client.login(handle, password, fetch_bsky_profile=False)
        except at_exc.RateLimitExceededError as exc:
            BlueskySessionState.objects.filter(pk=1).update(
                login_retry_after=now + RATE_LIMIT_BACKOFF,
                last_error="Bluesky is limiting logins right now. The bot will try again shortly.",
            )
            raise BlueskyUnavailable("Bluesky rate-limited the login.", category="bluesky_rate_limited") from exc
        except (at_exc.NetworkError, at_exc.RequestException) as exc:
            raise BlueskyUnavailable(f"Couldn't reach Bluesky: {exc}") from exc
        except Exception as exc:
            if not (is_auth_error(exc) or isinstance(exc, at_exc.BadRequestError)):
                raise
            failures = (state.login_failures if same_creds else 0) + 1
            wait = LOGIN_BACKOFF[min(failures, len(LOGIN_BACKOFF)) - 1]
            message = "Bluesky didn't accept the handle or App Password."
            BlueskySessionState.objects.filter(pk=1).update(
                handle=handle, credential_fingerprint=fp, session_string="",
                login_failures=failures, login_retry_after=now + wait, last_error=message,
            )
            logger.error("Bluesky login rejected for %s; next attempt after %s", handle, wait)
            _log_login("login_failed", "bluesky_login_rejected")
            raise BlueskyLoginError(message) from exc

        self._password_login_done = True
        BlueskySessionState.objects.filter(pk=1).update(
            login_failures=0, login_retry_after=None, last_error=""
        )
        if state.login_failures:  # it failed before (with these or older credentials)
            _log_login("login_recovered")
        logger.info("Logged in to Bluesky as %s", handle)
        return client


# -- "Test login" in Settings -------------------------------------------------------------------
APP_PASSWORD_SHAPE = re.compile(r"^[a-z0-9]{4}(-[a-z0-9]{4}){3}$")
UNAVAILABLE_LOGIN_ERRORS = {"AccountTakedown", "AccountDeactivated"}


@dataclass
class LoginTest:
    """The result of trying a handle and password once.

    result: ok | rejected | app_password_needed | rate_limited | unavailable | error
    """
    result: str
    category: str = ""
    did: str = ""
    handle: str = ""
    retry_after: int | None = None


def looks_like_app_password(password: str) -> bool:
    return bool(APP_PASSWORD_SHAPE.match((password or "").strip()))


@sensitive_variables("password", "client")
def try_login(handle: str, password: str) -> LoginTest:
    """Log in once with a throwaway client and report what Bluesky said.

    Nothing is saved: the client has no session-change hook, the shared session and its
    back-off are untouched, and the client (with its tokens) is dropped on return.
    Nothing about the password or the session ever goes into the result or the logs.
    """
    from bluesky_monitor.profiles import _retry_after_seconds

    client = SharedSession.client_factory()
    try:
        client.login(handle, password, fetch_bsky_profile=False)
    except at_exc.RateLimitExceededError as exc:
        return LoginTest("rate_limited", "bluesky_rate_limited", retry_after=_retry_after_seconds(exc, 300))
    except (at_exc.NetworkError, at_exc.InvokeTimeoutError) as exc:
        logger.info("Login test couldn't reach Bluesky: %s", type(exc).__name__)
        return LoginTest("unavailable", "bluesky_unavailable")
    except Exception as exc:
        name = problems_error_name(exc)
        if name == "AuthFactorTokenRequired":
            return LoginTest("app_password_needed", "bluesky_app_password_needed")
        if name in UNAVAILABLE_LOGIN_ERRORS:
            return LoginTest("rejected", "account_unavailable")
        if is_auth_error(exc) or isinstance(exc, (at_exc.BadRequestError, at_exc.UnauthorizedError)):
            return LoginTest("rejected", "bluesky_login_rejected")
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if isinstance(exc, at_exc.RequestException) and status and status >= 500:
            return LoginTest("unavailable", "bluesky_unavailable")
        logger.warning("Login test failed unexpectedly: %s", type(exc).__name__)
        return LoginTest("error", "bluesky_error")
    session = getattr(client, "_session", None) or getattr(client, "me", None)
    return LoginTest("ok", did=str(getattr(session, "did", "") or ""), handle=str(getattr(session, "handle", "") or ""))


def problems_error_name(exc) -> str:
    from activity.problems import xrpc_error_name

    return xrpc_error_name(exc)


@sensitive_variables("password")
def saved_login_works(handle: str, password: str) -> bool:
    """A login test of the *saved* credentials worked: end the wait after a rejected (or
    rate-limited) login, so the next check logs in instead of waiting up to an hour.

    Only the back-off is changed; the saved session and credentials are not. Returns True
    if the login had been failing (worth an "it works again" log entry).
    """
    fp = _fingerprint(handle, password)
    state = BlueskySessionState.objects.filter(pk=1, credential_fingerprint=fp).first()
    if state is None or (not state.login_failures and not state.login_retry_after):
        return False
    BlueskySessionState.objects.filter(pk=1, credential_fingerprint=fp).update(
        login_failures=0, login_retry_after=None, last_error="")
    if state.login_failures:
        from activity import log

        log.record(log.LOGIN_RECOVERED, "success", "The saved Bluesky login works again (tested in Settings). "
                   "Checks resume at the next check.")
        return True
    return False
