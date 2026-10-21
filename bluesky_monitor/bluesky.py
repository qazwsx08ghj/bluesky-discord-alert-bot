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
from datetime import timedelta

from atproto import Client
from atproto_client import exceptions as at_exc
from atproto_client.client.session import SessionEvent
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import BlueskySessionState

logger = logging.getLogger(__name__)

AUTH_ERROR_NAMES = {"ExpiredToken", "InvalidToken", "AuthenticationRequired", "AuthMissing", "AccountTakedown"}
LOGIN_BACKOFF = [timedelta(minutes=10), timedelta(minutes=20), timedelta(minutes=40), timedelta(hours=1)]
RATE_LIMIT_BACKOFF = timedelta(minutes=15)


class BlueskyLoginError(Exception):
    """The bot's own Bluesky login is missing or was rejected. Checking can't continue."""


class BlueskyUnavailable(Exception):
    """Bluesky couldn't be reached right now (network, timeout, rate limit). Try later."""


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
        raise BlueskyLoginError("The bot's Bluesky login isn't set up yet.")
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
            raise BlueskyLoginError(state.last_error or "Bluesky rejected the bot's login.")

        client = self._new_client(fp, handle)
        try:
            client.login(handle, password, fetch_bsky_profile=False)
        except at_exc.RateLimitExceededError as exc:
            BlueskySessionState.objects.filter(pk=1).update(
                login_retry_after=now + RATE_LIMIT_BACKOFF,
                last_error="Bluesky is limiting logins right now. The bot will try again shortly.",
            )
            raise BlueskyUnavailable("Bluesky rate-limited the login.") from exc
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
            raise BlueskyLoginError(message) from exc

        self._password_login_done = True
        BlueskySessionState.objects.filter(pk=1).update(
            login_failures=0, login_retry_after=None, last_error=""
        )
        logger.info("Logged in to Bluesky as %s", handle)
        return client
