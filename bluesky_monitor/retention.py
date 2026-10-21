"""Post history clean-up: each account keeps its newest SEEN_POSTS_KEPT_PER_ACCOUNT (1,000)
finished post records; older finished records are deleted.

Why it's safe: a check reads an account's feed from the newest post down and stops at the
first post it already knows, and the first check notes only the newest page (30 posts).
The newest 1,000 records per account always include those, so nothing old is announced
again. Posts still waiting to be sent (pending, sending, retry) are never deleted.

Runs after every check (bluesky_monitor/scheduler.py). A failure is only logged.
"""
import logging

from django.conf import settings
from django.db import DatabaseError

from .models import MonitoredAccount, SeenPost

logger = logging.getLogger(__name__)

FINISHED = (SeenPost.SEEDED, SeenPost.SENT, SeenPost.FAILED, SeenPost.SKIPPED)


def prune_seen_posts(keep=None) -> int:
    """Delete finished records past the newest `keep` per account. Returns how many."""
    keep = settings.SEEN_POSTS_KEPT_PER_ACCOUNT if keep is None else keep
    removed = 0
    for account_id in MonitoredAccount.objects.values_list("id", flat=True):
        ids = SeenPost.objects.filter(account_id=account_id).order_by("-id").values_list("id", flat=True)
        boundary = list(ids[keep:keep + 1])
        if boundary:
            count, _ = SeenPost.objects.filter(account_id=account_id, id__lte=boundary[0],
                                               status__in=FINISHED).delete()
            removed += count
    if removed:
        logger.info("Post history clean-up: removed %d old records", removed)
    return removed


def prune_quietly():
    try:
        return prune_seen_posts()
    except DatabaseError:
        logger.warning("Post history clean-up failed; will try again after the next check", exc_info=True)
        return None
