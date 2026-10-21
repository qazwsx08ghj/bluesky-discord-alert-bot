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
"""
import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from discord_alerts import sender
from .bluesky import BlueskyLoginError, BlueskyUnavailable, SharedSession
from .locks import acquire_account_lock, release_account_lock
from .models import MonitoredAccount, SeenPost

logger = logging.getLogger(__name__)

RETRY_DELAYS = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30)]
FEED_PAGE_SIZE = 30
FEED_MAX_ITEMS = 100


class AccountBusy(Exception):
    """Another check of this account is running right now."""


def _new_session() -> SharedSession:
    return SharedSession()


def _post_url(handle: str, uri: str) -> str:
    # uri format: at://did:plc:xxx/app.bsky.feed.post/rkey
    rkey = uri.split("/")[-1]
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


def _webhook_for(account: MonitoredAccount) -> str:
    from botconfig import store

    return account.discord_webhook_url or store.default_webhook_url()


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
    webhook_url = _webhook_for(account)
    if not webhook_url:
        return 0

    now = timezone.now()
    window_start = now - timedelta(hours=settings.SEND_RETRY_WINDOW_HOURS)
    sent = 0
    for post in list(_due_for_delivery(account, now)):
        snapshot = SeenPost.objects.filter(pk=post.pk, status=post.status, claim_token=post.claim_token)

        if post.seen_at < window_start:
            snapshot.update(status=SeenPost.FAILED, last_error=post.last_error or "Not sent within 24 hours.")
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
        if result.ok:
            mine.update(status=SeenPost.SENT, sent_at=timezone.now(), next_attempt_at=None, last_error="")
            sent += 1
            logger.info("Announced post from %s: %s", account.handle, post.post_uri)
            continue

        attempts = post.attempts + 1
        if attempts >= settings.SEND_MAX_ATTEMPTS:
            mine.update(status=SeenPost.FAILED, last_error=result.error[:500])
            logger.error("Gave up sending %s after %d attempts", post.post_uri, attempts)
        else:
            delay = RETRY_DELAYS[min(attempts, len(RETRY_DELAYS)) - 1]
            if result.retry_after:
                delay = max(delay, timedelta(seconds=result.retry_after))
            mine.update(status=SeenPost.RETRY, next_attempt_at=timezone.now() + delay, last_error=result.error[:500])
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
        account.refresh_from_db(fields=["first_poll_done", "is_active", "display_name", "discord_webhook_url"])
        # Bluesky trouble never blocks Discord retries of posts already claimed.
        try:
            items = fetch_new_items(session or _new_session(), account)
        except BlueskyLoginError:
            deliver_pending(account)
            raise
        except (BlueskyUnavailable, Exception) as exc:
            logger.error("Failed to fetch feed for %s: %s", account.handle, exc)
            return deliver_pending(account)

        is_first_poll = not account.first_poll_done
        if is_first_poll:
            status = SeenPost.SEEDED
        elif _webhook_for(account):
            status = SeenPost.PENDING
        else:
            status = SeenPost.SKIPPED  # no channel set: noted, never sent (as before)
        for item in items:
            claim_post(account, item, status)

        if is_first_poll:
            MonitoredAccount.objects.filter(pk=account.pk).update(first_poll_done=True)
            account.first_poll_done = True
            logger.info("Seeded existing posts for %s - no announcements on first poll", account.handle)

        return deliver_pending(account)
    finally:
        release_account_lock(account.pk, token)


def poll_all_active_accounts(should_continue=None):
    """Poll every active monitored account with one shared Bluesky session. Called by the scheduler.

    should_continue: optional callable checked before each account (the scheduler passes
    "do I still hold the lease?").
    """
    session = _new_session()
    for account in MonitoredAccount.objects.filter(is_active=True).order_by("id"):
        if should_continue is not None and not should_continue():
            logger.warning("Stopping this check cycle: the checker lease was lost")
            return
        try:
            poll_account(account, session=session)
        except AccountBusy:
            logger.info("Skipping %s: it is already being checked", account.handle)
        except BlueskyLoginError as exc:
            logger.error("Check cycle stopped: %s", exc)
            return
