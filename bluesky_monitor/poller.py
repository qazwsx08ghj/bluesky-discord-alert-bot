"""Check Bluesky accounts and deliver new posts to Discord.

How a post moves (see SeenPost for the states):

1. Claim: a SeenPost row is inserted before anything is sent. The unique
   (account, post_uri) constraint lets exactly one process claim a post.
2. Deliver: a claimed post is switched to "sending" with a conditional UPDATE that
   carries a fresh claim token. Only the process whose UPDATE matched may send it.
3. Result: "sent" on success. On failure it becomes "retry" with a next attempt time,
   up to SEND_MAX_ATTEMPTS tries within SEND_RETRY_WINDOW_HOURS, then "failed".
4. Interrupted sends: a post left in "sending" longer than SEND_STALE_SECONDS (the
   process died mid-send) is picked up again by the next delivery.

Nothing is lost: every unsent post stays in the database until it is sent or gives up.
Duplicates can only happen if a process dies after Discord stored the message but
before the "sent" update, and even then only once, after the stale timeout.

Every send, retry, give-up and failed check is also written to the activity log
(activity.log), which is separate from this queue.
"""
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from activity import log as activity, problems
from activity.models import ActivityEvent
from discord_alerts import channels, sender
from .bluesky import BlueskyLoginError, BlueskyUnavailable, SharedSession
from .locks import acquire_account_lock, release_account_lock
from .models import MonitoredAccount, SeenPost

logger = logging.getLogger(__name__)

RETRY_DELAYS = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30)]
FEED_PAGE_SIZE = 30
FEED_MAX_ITEMS = 100


class AccountBusy(Exception):
    """Another check of this account is running right now."""


@dataclass
class CycleSummary:
    """What one check of all accounts did. Saved on SchedulerState for /api/status/.

    result: ok | partial (some accounts failed) | failed (all failed, or the check stopped)
            | not_configured (no Bluesky login) | interrupted (another checker took over)
    """
    result: str = "ok"
    category: str = ""
    accounts_checked: int = 0
    accounts_failed: int = 0
    accounts_busy: int = 0
    posts_sent: int = 0

    def as_dict(self):
        return asdict(self)


def _new_session() -> SharedSession:
    return SharedSession()


def _post_url(handle: str, uri: str) -> str:
    # uri format: at://did:plc:xxx/app.bsky.feed.post/rkey
    rkey = uri.split("/")[-1]
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


def _channel_for(account: MonitoredAccount):
    """The account's own channel, else the default channel, else None (see discord_alerts.channels)."""
    from discord_alerts import channels

    return channels.route_for(account)


def _webhook_for(account: MonitoredAccount) -> str:
    channel = _channel_for(account)
    return channel.url if channel else ""


# -- activity log helpers -------------------------------------------------------
def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _wait_text(delay: timedelta) -> str:
    seconds = int(delay.total_seconds())
    return _plural(max(1, round(seconds / 60)), "minute") if seconds >= 60 else _plural(max(seconds, 1), "second")


def _post_detail(post, **extra):
    return {"post_url": post.post_url, "post_text": (post.text or "")[:200], **extra}


def _check_ok(account: MonitoredAccount):
    was_failing = account.last_check_ok is False
    MonitoredAccount.objects.filter(pk=account.pk).update(
        last_checked_at=timezone.now(), last_check_ok=True, last_check_category=None)
    account.last_check_ok = True
    if was_failing:
        activity.record(activity.CHECK_RECOVERED, ActivityEvent.SUCCESS,
                        f"Checking @{account.handle} works again.", account=account)


def _check_failed(account: MonitoredAccount, exc: Exception):
    category = problems.classify_bluesky_error(exc)
    MonitoredAccount.objects.filter(pk=account.pk).update(
        last_checked_at=timezone.now(), last_check_ok=False, last_check_category=category)
    account.last_check_ok = False
    temporary = category in ("bluesky_unavailable", "bluesky_rate_limited", "bluesky_error")
    activity.problem(activity.CHECK_FAILED, category,
                     f"Couldn't check @{account.handle}: {problems.get(category).title}.",
                     level=ActivityEvent.WARNING if temporary else ActivityEvent.ERROR,
                     account=account, exc=exc, merge_repeats=True)


# -- reading the feed -----------------------------------------------------------
def fetch_new_items(session, account: MonitoredAccount) -> list:
    """Feed items not yet recorded for this account, oldest first.

    Reads pages until it reaches a post it has already seen (or FEED_MAX_ITEMS), so a
    burst of posts between two checks is not cut off at one page.
    """
    items, cursor = [], None
    while True:
        page = session.author_feed(account.handle, cursor=cursor, limit=FEED_PAGE_SIZE)
        feed = list(page.feed)
        uris = [item.post.uri for item in feed]
        seen = set(SeenPost.objects.filter(account=account, post_uri__in=uris).values_list("post_uri", flat=True))
        items.extend(item for item in feed if item.post.uri not in seen)
        cursor = getattr(page, "cursor", None)
        reached_known_posts = bool(seen)
        first_check = not account.first_poll_done  # the first check only notes the latest page
        if reached_known_posts or first_check or not cursor or not feed or len(items) >= FEED_MAX_ITEMS:
            break
    return list(reversed(items[:FEED_MAX_ITEMS]))


# -- claiming -------------------------------------------------------------------
def claim_post(account: MonitoredAccount, item, status: str) -> bool:
    """Record a post. Returns False if it was already recorded (by any process)."""
    post = item.post
    author = getattr(getattr(post, "author", None), "handle", None) or account.handle
    try:
        with transaction.atomic():
            SeenPost.objects.create(
                account=account,
                post_uri=post.uri,
                post_cid=post.cid,
                text=getattr(post.record, "text", "") or "",
                post_url=_post_url(author, post.uri),
                status=status,
            )
        return True
    except IntegrityError:
        return False


# -- delivering -----------------------------------------------------------------
def _due_for_delivery(account: MonitoredAccount, now):
    stale_before = now - timedelta(seconds=settings.SEND_STALE_SECONDS)
    return SeenPost.objects.filter(account=account).filter(
        Q(status=SeenPost.PENDING)
        | Q(status=SeenPost.RETRY, next_attempt_at__lte=now)
        | Q(status=SeenPost.SENDING, claimed_at__lt=stale_before)
    ).order_by("id")


def deliver_pending(account: MonitoredAccount) -> int:
    """Send every post that is due for this account. Returns how many were sent."""
    channel = _channel_for(account)
    if channel is None:
        return 0
    webhook_url = channel.url

    now = timezone.now()
    window_start = now - timedelta(hours=settings.SEND_RETRY_WINDOW_HOURS)
    sent = 0
    for post in list(_due_for_delivery(account, now)):
        snapshot = SeenPost.objects.filter(pk=post.pk, status=post.status, claim_token=post.claim_token)

        if post.seen_at < window_start:
            if snapshot.update(status=SeenPost.FAILED, last_error=post.last_error or "Not sent within 24 hours.") == 1:
                activity.problem(activity.SEND_GAVE_UP, "send_expired",
                                 f'A post from @{account.handle} was never sent to "{channel.name}": '
                                 f"it couldn't be sent within {settings.SEND_RETRY_WINDOW_HOURS} hours.",
                                 account=account, channel=channel, attempt=post.attempts or None,
                                 detail=_post_detail(post), technical=post.last_error)
            continue

        token = uuid.uuid4().hex
        if snapshot.update(status=SeenPost.SENDING, claim_token=token, claimed_at=timezone.now(),
                           attempts=F("attempts") + 1) != 1:
            continue  # another process claimed it between our read and our update

        result = sender.deliver_alert(
            webhook_url=webhook_url,
            handle=account.handle,
            display_name=account.display_name or account.handle,
            text=post.text,
            post_url=post.post_url,
        )
        mine = SeenPost.objects.filter(pk=post.pk, claim_token=token)
        channels.record_delivery(channel.pk, result.ok, result.error)
        attempts = post.attempts + 1
        max_attempts = settings.SEND_MAX_ATTEMPTS
        where = f'@{account.handle} to "{channel.name}"'
        if result.ok:
            mine.update(status=SeenPost.SENT, sent_at=timezone.now(), next_attempt_at=None, last_error="")
            sent += 1
            logger.info("Announced post from %s: %s", account.handle, post.post_uri)
            retry_note = f" It worked on try {attempts} of {max_attempts}." if attempts > 1 else ""
            activity.record(activity.POST_SENT, ActivityEvent.SUCCESS, f"Sent a post from {where}.{retry_note}",
                            account=account, channel=channel, attempt=attempts, detail=_post_detail(post))
            continue

        category = result.category or "discord_error"
        if attempts >= max_attempts:
            mine.update(status=SeenPost.FAILED, last_error=result.error[:500])
            logger.error("Gave up sending %s after %d attempts", post.post_uri, attempts)
            activity.problem(activity.SEND_GAVE_UP, category,
                             f"Gave up sending a post from {where} after {attempts} tries. {result.error}",
                             account=account, channel=channel, attempt=attempts,
                             detail=_post_detail(post, http_status=result.status_code), technical=result.technical)
        else:
            delay = RETRY_DELAYS[min(attempts, len(RETRY_DELAYS)) - 1]
            if result.retry_after:
                delay = max(delay, timedelta(seconds=result.retry_after))
            next_at = timezone.now() + delay
            mine.update(status=SeenPost.RETRY, next_attempt_at=next_at, last_error=result.error[:500])
            activity.problem(activity.SEND_FAILED, category,
                             f"Couldn't send a post from {where} (try {attempts} of {max_attempts}). "
                             f"{result.error} The bot will try again in about {_wait_text(delay)}.",
                             level=ActivityEvent.WARNING, account=account, channel=channel, attempt=attempts,
                             detail=_post_detail(post, http_status=result.status_code,
                                                 next_attempt_at=next_at.isoformat()),
                             technical=result.technical)
        # The channel is failing; leave this account's other posts for the next delivery.
        break
    return sent


# -- checking -------------------------------------------------------------------
def poll_account(account: MonitoredAccount, session=None) -> int:
    """Fetch recent posts for one account and announce any new ones. Returns count of posts sent.

    Raises AccountBusy if another process is checking this account right now.
    """
    token = acquire_account_lock(account.pk)
    if token is None:
        raise AccountBusy(account.handle)
    try:
        account.refresh_from_db(fields=["first_poll_done", "is_active", "display_name", "channel", "last_check_ok"])
        # Bluesky trouble never blocks Discord retries of posts already claimed.
        try:
            items = fetch_new_items(session or _new_session(), account)
        except BlueskyLoginError:
            deliver_pending(account)
            raise
        except (BlueskyUnavailable, Exception) as exc:
            # Only the error type here; the full (scrubbed) cause is in the activity log entry.
            logger.error("Failed to fetch feed for %s: %s", account.handle, type(exc).__name__)
            _check_failed(account, exc)
            return deliver_pending(account)
        _check_ok(account)

        is_first_poll = not account.first_poll_done
        if is_first_poll:
            status = SeenPost.SEEDED
        elif _webhook_for(account):
            status = SeenPost.PENDING
        else:
            status = SeenPost.SKIPPED  # no channel set: noted, never sent (as before)
        claimed = sum(1 for item in items if claim_post(account, item, status))

        if is_first_poll:
            MonitoredAccount.objects.filter(pk=account.pk).update(first_poll_done=True)
            account.first_poll_done = True
            logger.info("Seeded existing posts for %s - no announcements on first poll", account.handle)
            activity.record(activity.FIRST_CHECK_DONE, ActivityEvent.INFO,
                            f"First check of @{account.handle} done. Noted {_plural(claimed, 'existing post')}; "
                            "none were sent to Discord. New posts from now on will be announced.",
                            account=account, detail={"seeded_posts": claimed})
        elif status == SeenPost.SKIPPED and claimed:
            activity.problem(activity.POST_NOT_SENT, "no_channel",
                             f"{_plural(claimed, 'new post')} from @{account.handle} not sent: "
                             "no Discord channel is chosen for this account.",
                             level=ActivityEvent.WARNING, account=account, detail={"posts": claimed},
                             merge_repeats=True)

        return deliver_pending(account)
    finally:
        release_account_lock(account.pk, token)


def poll_all_active_accounts(should_continue=None) -> CycleSummary:
    """Poll every active monitored account with one shared Bluesky session. Called by the scheduler.

    should_continue: optional callable checked before each account (the scheduler passes
    "do I still hold the lease?"). Returns what the check did.
    """
    summary = CycleSummary()
    session = _new_session()
    for account in MonitoredAccount.objects.filter(is_active=True).order_by("id"):
        if should_continue is not None and not should_continue():
            logger.warning("Stopping this check cycle: the checker lease was lost")
            summary.result = "interrupted"
            return summary
        try:
            sent = poll_account(account, session=session)
        except AccountBusy:
            logger.info("Skipping %s: it is already being checked", account.handle)
            summary.accounts_busy += 1
            continue
        except BlueskyLoginError as exc:
            logger.error("Check cycle stopped: %s", exc)
            summary.category = getattr(exc, "category", "bluesky_login_rejected")
            summary.result = "not_configured" if summary.category == "bluesky_not_configured" else "failed"
            return summary
        summary.accounts_checked += 1
        summary.posts_sent += sent if isinstance(sent, int) else 0
        health = MonitoredAccount.objects.filter(pk=account.pk).values_list(
            "last_check_ok", "last_check_category").first()
        if health and health[0] is False:
            summary.accounts_failed += 1
            summary.category = health[1] or "internal"
    if summary.accounts_failed:
        summary.result = "failed" if summary.accounts_failed == summary.accounts_checked else "partial"
    return summary
