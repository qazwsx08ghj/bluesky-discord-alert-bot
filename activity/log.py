"""Writing activity events. Use `record()`; it never raises.

Everything stored is passed through botconfig.secrets.scrub first, together with the
exact values of the saved App Password and Bluesky session, so secrets can't end up in
the log even if a library puts them into an error message.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError
from django.db.models import F
from django.utils import timezone

from botconfig.secrets import scrub
from . import problems
from .models import ActivityEvent

logger = logging.getLogger(__name__)

REPEAT_WINDOW = timedelta(hours=1)

# Event types. Groups used by the API filters are in activity/views.py.
POST_SENT = "post_sent"                  # success; attempt > 1 means it was a retry
SEND_FAILED = "send_failed"              # warning; will be retried
SEND_GAVE_UP = "send_gave_up"            # error; no more retries
POST_NOT_SENT = "post_not_sent"          # warning; no channel for the account
MENTION_NOT_SENT = "mention_not_sent"    # warning; sent, but the role mention was left out for safety
CHECK_FAILED = "check_failed"            # an account's feed couldn't be read
CHECK_RECOVERED = "check_recovered"      # it works again
FIRST_CHECK_DONE = "first_check_done"
LOGIN_FAILED = "login_failed"
LOGIN_RECOVERED = "login_recovered"
CYCLE_FAILED = "cycle_failed"            # a whole check stopped on an unexpected error
CHECKER_STARTED = "checker_started"
CHECKER_STOPPED = "checker_stopped"
ACCOUNT_ADDED = "account_added"
ACCOUNT_REMOVED = "account_removed"
CHANNEL_TEST_SENT = "channel_test_sent"
CHANNEL_TEST_FAILED = "channel_test_failed"


def _known_secrets():
    secrets = [settings.SECRET_KEY]
    try:
        from botconfig import store
        from bluesky_monitor.models import BlueskySessionState

        secrets.append(store.get().bluesky_password)
        session = BlueskySessionState.objects.filter(pk=1).values_list("session_string", flat=True).first()
        secrets.append(session or "")
    except Exception:  # the patterns in scrub() still apply
        pass
    return [s for s in secrets if s]


def _clean(value, secrets):
    if isinstance(value, str):
        return scrub(value, secrets)
    if isinstance(value, dict):
        return {k: _clean(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v, secrets) for v in value]
    return value


def _label_account(account):
    return f"@{account.handle}" if account is not None else ""


def record(type, level, message, *, account=None, channel=None, category="", attempt=None,
           detail=None, technical="", merge_repeats=False):
    """Add an event to the activity log. Returns the event, or None if it couldn't be saved.

    merge_repeats: if the newest event for the same account and channel is the same type and
    category and is less than an hour old, count this as a repeat of it instead.
    """
    try:
        secrets = _known_secrets()
        now = timezone.now()
        fields = dict(
            type=type, level=level, category=category or "",
            message=scrub(message, secrets)[:500],
            account_id=getattr(account, "pk", None), account_label=_label_account(account)[:255],
            channel_id=getattr(channel, "pk", None), channel_label=(getattr(channel, "name", "") or "")[:100],
            attempt=attempt, detail=_clean(detail or {}, secrets), technical=scrub(technical, secrets)[:1000],
        )
        if merge_repeats:
            newest = (ActivityEvent.objects
                      .filter(account_id=fields["account_id"], channel_id=fields["channel_id"])
                      .order_by("-id").first())
            if (newest is not None and newest.type == type and newest.category == fields["category"]
                    and newest.created_at >= now - REPEAT_WINDOW):
                ActivityEvent.objects.filter(pk=newest.pk).update(
                    repeat_count=F("repeat_count") + 1, last_seen_at=now,
                    message=fields["message"], technical=fields["technical"], detail=fields["detail"])
                newest.refresh_from_db()
                return newest
        return ActivityEvent.objects.create(created_at=now, last_seen_at=now, **fields)
    except DatabaseError:
        # The log is never allowed to stop a check or a delivery.
        logger.warning("Couldn't save an activity event (%s)", type, exc_info=True)
        return None


def problem(type, category, message, *, level=ActivityEvent.ERROR, exc=None, technical="", **kwargs):
    """record() for a problem: adds the category and the technical cause of `exc`."""
    if category not in problems.CATEGORIES:
        category = "internal"
    if exc is not None and not technical:
        technical = problems.technical(exc)
    return record(type, level, message, category=category, technical=technical, **kwargs)
