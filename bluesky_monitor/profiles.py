"""Bluesky handles and profile lookup, for the control panel's "Add account" search.

The lookup reads Bluesky's public API (BLUESKY_PUBLIC_API_URL) without logging in, so it
works before the bot's login is set up, never uses the bot's credentials or session, and
doesn't use up the bot's own Bluesky limits.

Every answer has the same shape (see lookup()). The HTTP status says whether Bluesky could
be asked; `result` says what the answer was:

  200 found         the profile exists                        exists: true
  200 not_found     no account with that handle               exists: false
  200 unavailable   exists, but deactivated/suspended/blocks  exists: true
  400 invalid       not a handle; `suggestion` may offer one  exists: null
  429 rate_limited  Bluesky asked us to slow down             exists: null, retry_after_seconds
  503 error         Bluesky couldn't be reached               exists: null, retryable: true
  502 error         Bluesky answered with something unexpected

Answers are cached briefly (PROFILE_LOOKUP_CACHE_SECONDS), so a search box that looks up
as the user types costs Bluesky at most one request per handle. `query` is the text
exactly as sent, so the frontend can drop answers to queries the user has typed past.
"""
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q

from activity import problems

logger = logging.getLogger(__name__)

# atproto handle syntax: dot-separated labels; the last one doesn't start with a digit.
HANDLE_RE = re.compile(r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?$")
LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
DID_RE = re.compile(r"^did:[a-z]+:[a-zA-Z0-9._:%-]*[a-zA-Z0-9._-]$")
# Endings Bluesky never accepts as handles.
RESERVED_TLDS = (".alt", ".arpa", ".example", ".internal", ".invalid", ".local", ".localhost", ".onion")
# Invisible characters that sneak in when copying from web pages.
INVISIBLE = dict.fromkeys(map(ord, "​‌‍‎‏⁠﻿‪‫‬‭‮"))
INVALID_HANDLE = "handle.invalid"  # what Bluesky shows when an account's handle no longer checks out

NOT_A_HANDLE = ("That doesn't look like a Bluesky handle. A handle looks like name.bsky.social: letters, "
                "numbers and hyphens, separated by dots.")

CACHE_PREFIX = "bsky-profile:"
RATE_LIMIT_KEY = "bsky-profile-rate-limited-until"


@dataclass
class Normalized:
    actor: str = ""       # a lowercase handle or a DID, ready to look up
    error: str = ""       # why the input isn't usable, in plain language
    suggestion: str = ""  # e.g. "alice.bsky.social" when the user typed "alice"

    @property
    def ok(self):
        return bool(self.actor) and not self.error


def normalize_handle(raw) -> Normalized:
    """Turn what a user typed or pasted into a handle: "@Alice.bsky.social", a profile link,
    or a DID all work. Never contacts Bluesky."""
    text = str(raw or "").translate(INVISIBLE).strip()
    if not text:
        return Normalized(error="Type a Bluesky handle, for example name.bsky.social.")
    if len(text) > 300:
        return Normalized(error=NOT_A_HANDLE)

    lowered = text.lower()
    if lowered.startswith(("http://", "https://", "bsky.app/", "www.bsky.app/")):
        url = urlparse(text if "://" in text else "https://" + text)
        parts = [p for p in url.path.split("/") if p]
        if len(parts) < 2 or parts[0].lower() != "profile":
            return Normalized(error="That link isn't a Bluesky profile. Paste a link like "
                                    "https://bsky.app/profile/name.bsky.social, or just the handle.")
        text = parts[1]

    text = text.strip().lstrip("@").strip()
    if text[:4].lower() == "did:":
        did = "did:" + text[4:]
        return Normalized(actor=did) if DID_RE.match(did) and len(did) <= 2048 else Normalized(
            error="That doesn't look like a Bluesky account ID. IDs look like did:plc:abc123….")

    handle = text.lower()
    if any(c.isspace() for c in handle):
        return Normalized(error="A Bluesky handle can't contain spaces.")
    if "." not in handle and LABEL_RE.match(handle):
        suggestion = f"{handle}.bsky.social"
        return Normalized(error=f"Handles include a domain. Did you mean {suggestion}?", suggestion=suggestion)
    if len(handle) > 253 or not HANDLE_RE.match(handle) or handle.endswith(RESERVED_TLDS):
        return Normalized(error=NOT_A_HANDLE)
    return Normalized(actor=handle)


def watched_account(actor: str, *more):
    """The watched account with this handle (any case, with or without "@"), or None."""
    from .models import MonitoredAccount

    query = Q()
    for value in filter(None, (actor, *more)):
        query |= Q(handle__iexact=value) | Q(handle__iexact="@" + value)
    return MonitoredAccount.objects.filter(query).order_by("id").first() if query else None


# -- talking to Bluesky ---------------------------------------------------------------------------------
def _public_client():
    from atproto import Client
    from atproto_client.request import Request

    return Client(base_url=settings.BLUESKY_PUBLIC_API_URL,
                  request=Request(timeout=settings.BLUESKY_REQUEST_TIMEOUT_SECONDS))


client_factory = _public_client  # replaced in tests


def _retry_after_seconds(exc, default=60) -> int:
    from datetime import datetime, timezone as tz

    seconds = getattr(exc, "retry_after", None)
    if seconds is None and getattr(exc, "reset_at", None) is not None:
        seconds = (exc.reset_at - datetime.now(tz.utc)).total_seconds()
    try:
        return max(1, min(int(seconds), 3600)) if seconds is not None else default
    except (TypeError, ValueError):
        return default


def _profile_dict(profile) -> dict:
    handle = getattr(profile, "handle", "") or ""
    did = getattr(profile, "did", "") or ""
    return {
        "did": did,
        "handle": handle,
        "display_name": (getattr(profile, "display_name", None) or "").strip(),
        "avatar_url": getattr(profile, "avatar", None) or None,
        "description": (getattr(profile, "description", None) or "")[:300],
        "followers_count": getattr(profile, "followers_count", None),
        "posts_count": getattr(profile, "posts_count", None),
        # What to send as `handle` when adding the account. A handle that no longer checks
        # out can't be used for checks, but the account's permanent ID can.
        "watch_as": did if handle == INVALID_HANDLE or not handle else handle,
    }


def _fetch(actor: str):
    """Ask Bluesky. Returns (result, extra) where extra is the profile or error details."""
    from atproto_client import exceptions as at_exc

    try:
        return "found", client_factory().get_profile(actor)
    except at_exc.RateLimitExceededError as exc:
        return "rate_limited", {"retry_after": _retry_after_seconds(exc)}
    except (at_exc.NetworkError, at_exc.InvokeTimeoutError) as exc:
        return "error", {"category": "bluesky_unavailable", "technical": type(exc).__name__}
    except at_exc.BadRequestError as exc:
        name = problems.xrpc_error_name(exc)
        if name in problems.UNAVAILABLE_ACCOUNT_ERRORS:
            return "unavailable", {"reason": name}
        return "not_found", {"reason": name or "InvalidRequest"}
    except at_exc.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 404:
            return "not_found", {"reason": "NotFound"}
        category = "bluesky_unavailable" if status and status >= 500 else "bluesky_error"
        return "error", {"category": category, "technical": f"{type(exc).__name__} HTTP {status}"}
    except Exception as exc:  # unexpected (including auth errors, which a public read shouldn't get)
        logger.warning("Profile lookup failed: %s", type(exc).__name__)
        return "error", {"category": "bluesky_error", "technical": type(exc).__name__}


# -- the lookup ---------------------------------------------------------------------------------------
def lookup(raw) -> tuple:
    """Look up a profile. Returns (http_status, body); see the module docstring for the shapes."""
    from django.utils import timezone

    query = "" if raw is None else str(raw)
    body = {
        "query": query, "handle": "", "result": "invalid", "exists": None, "profile": None,
        "already_watched": False, "watched_account_id": None,
        "code": "", "message": "", "problem": None, "suggestion": None,
        "retryable": False, "retry_after_seconds": None, "cached": False,
    }
    norm = normalize_handle(query)
    if not norm.ok:
        body.update(code="invalid_handle", message=norm.error, suggestion=norm.suggestion or None)
        return 400, body
    body["handle"] = norm.actor

    key = CACHE_PREFIX + norm.actor
    stored = cache.get(key)
    if stored is None:
        until = cache.get(RATE_LIMIT_KEY)
        now = timezone.now().timestamp()
        if until and until > now:
            result, extra = "rate_limited", {"retry_after": max(1, int(until - now))}
        else:
            result, extra = _fetch(norm.actor)
        if result == "found":
            stored = {"result": "found", "profile": _profile_dict(extra)}
        elif result in ("not_found", "unavailable"):
            stored = {"result": result, "reason": extra["reason"]}
        if stored is not None:
            cache.set(key, stored, settings.PROFILE_LOOKUP_CACHE_SECONDS)
    else:
        body["cached"] = True
        result, extra = stored["result"], {}

    if result == "rate_limited":
        retry = extra["retry_after"]
        cache.set(RATE_LIMIT_KEY, timezone.now().timestamp() + retry, retry)
        body.update(result="rate_limited", code="bluesky_rate_limited", retryable=True, retry_after_seconds=retry,
                    problem=problems.describe("bluesky_rate_limited"),
                    message=f"Bluesky asked the bot to slow down. Try again in {retry} seconds.")
        return 429, body
    if result == "error":
        category = extra["category"]
        logger.info("Profile lookup couldn't reach Bluesky: %s", extra.get("technical", ""))
        body.update(result="error", code=category, retryable=True, problem=problems.describe(category),
                    message="Couldn't reach Bluesky to look up this account. Check the internet connection "
                            "and try again." if category == "bluesky_unavailable" else
                            "Bluesky answered with an unexpected error. Try again in a moment.")
        return (503 if category == "bluesky_unavailable" else 502), body

    profile = stored.get("profile") or {}
    profile_handle = profile.get("handle", "")
    watched = watched_account(norm.actor, profile_handle if profile_handle != INVALID_HANDLE else "",
                              profile.get("did", ""))
    profile = profile or None
    body.update(already_watched=watched is not None, watched_account_id=watched.pk if watched else None)
    if result == "found":
        body.update(result="found", exists=True, profile=profile, code="found",
                    message=f"Found @{profile['handle']}." if profile["handle"] != INVALID_HANDLE else
                    "Found this account, but its handle no longer checks out. It can still be watched by its ID.")
        if watched:
            body["message"] = f"You're already watching @{watched.handle.lstrip('@')}."
    elif result == "not_found":
        body.update(result="not_found", exists=False, code="handle_not_found",
                    problem=problems.describe("account_not_found"),
                    message=f"No Bluesky account is called @{norm.actor}. Check the spelling.")
    else:
        body.update(result="unavailable", exists=True, code="account_unavailable",
                    problem=problems.describe("account_unavailable"),
                    message="This account exists, but its posts can't be seen right now (deactivated, suspended, "
                            "or it blocks the bot). It can still be added.")
    return 200, body
