"""Activity log clean-up: keep events for ACTIVITY_RETENTION_DAYS (30) and never more than
ACTIVITY_MAX_EVENTS (10,000), whichever limit is reached first.

Only ActivityEvent rows are deleted. The delivery queue (SeenPost) lives in another table
and is never touched here, so pending and retrying posts can't be lost to log clean-up.

Runs when the checker starts and after every check (at least once an hour, since the
longest check interval is 60 minutes), and on demand with `manage.py prune_activity`.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone

from .models import ActivityEvent

logger = logging.getLogger(__name__)


def prune(now=None) -> dict:
    """Delete events past either limit. Returns {"too_old": n, "over_limit": n}."""
    now = now or timezone.now()
    days, limit = settings.ACTIVITY_RETENTION_DAYS, settings.ACTIVITY_MAX_EVENTS
    too_old, _ = ActivityEvent.objects.filter(created_at__lt=now - timedelta(days=days)).delete()

    over_limit = 0
    # The newest `limit` events are kept; ids grow with time, so everything at or below the
    # id just past the limit is older than all of them.
    boundary = list(ActivityEvent.objects.order_by("-id").values_list("id", flat=True)[limit:limit + 1])
    if boundary:
        over_limit, _ = ActivityEvent.objects.filter(id__lte=boundary[0]).delete()
    if too_old or over_limit:
        logger.info("Activity log clean-up: removed %d old and %d over-limit events", too_old, over_limit)
    return {"too_old": too_old, "over_limit": over_limit}


def prune_quietly(now=None):
    """prune(), but a failure is only logged (it must never stop the checker)."""
    try:
        return prune(now)
    except DatabaseError:
        logger.warning("Activity log clean-up failed; will try again after the next check", exc_info=True)
        return None
