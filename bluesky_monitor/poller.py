"""Check Bluesky accounts and deliver new posts to Discord.

How a post moves (see SeenPost and PostDelivery for the states):

1. Claim: a SeenPost row is inserted before anything is sent, together with one
   PostDelivery per channel the account sends to (one transaction). The unique
   (account, post_uri) constraint lets exactly one process claim a post, and the unique
   (post, channel) constraint means each channel gets it at most once.
2. Deliver: each delivery is switched to "sending" with a conditional UPDATE that carries
   a fresh claim token. Only the process whose UPDATE matched may send it.
3. Result: "sent" on success. On failure it becomes "retry" with a next attempt time,
   up to SEND_MAX_ATTEMPTS tries within SEND_RETRY_WINDOW_HOURS, then "failed".
   Every delivery has its own attempts: a failing channel never holds up or repeats another.
4. Interrupted sends: a delivery left in "sending" longer than SEND_STALE_SECONDS (the
   process died mid-send) is picked up again by the next delivery.
5. Stopping (shutdown.py): once the checker is asked to stop, no new send is started and no
   new account is checked. A send already in progress finishes and its result is saved.

Nothing is lost: every unsent delivery stays in the database until it is sent, gives up or
is cancelled (its channel was removed from the account). Duplicates can only happen if a
process dies after Discord stored the message but before the "sent" update, and even then
only once per channel, after the stale timeout.

Every send, retry, give-up and failed check is also written to the activity log
(activity.log), which is separate from this queue.

What is sent is set per account (announce_posts / _reposts / _replies). Items of a
kind that is switched off are still recorded, as "skipped", so switching it on later
never floods the channel with old items.

Scheduled checks (poll_all_active_accounts) and "Check all" (check_all.py) both go through
check_accounts(): the same per-account lock, claims and delivery, so running both at once
can't send anything twice, and one account's failure never stops the others.
"""
import logging
import time
import traceback
import uuid
from dataclasses import asdict, dataclass
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Case, Exists, F, OuterRef, Q, Subquery, Value, When
from django.utils import timezone

from activity import log as activity, problems
from activity.models import ActivityEvent
from discord_alerts import channels, mentions, sender
from . import shutdown
from .bluesky import BlueskyLoginError, BlueskyUnavailable, SharedSession
from .locks import acquire_account_lock, release_account_lock
from .models import POST, REPLY, REPOST, MonitoredAccount, PostDelivery, SeenPost

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


# AccountCheck.result values
CHECKED, FAILED, SKIPPED, ALREADY_CHECKING = "checked", "failed", "skipped", "already_checking"


@dataclass
class AccountCheck:
    """What one check of one account did. Returned per account by "Check all".

    result: checked | failed (couldn't read the feed; see category) | skipped (see reason)
            | already_checking (another check of this account was running)
    reason (skipped only): paused | login_failed | not_configured | time_limit | stopped
    """
    account_id: int
    handle: str
    result: str = CHECKED
    reason: str = ""
    category: str = ""
    first_check: bool = False
    new_posts: int = 0      # posts noticed this time (on the first check: noted, never sent)
    posts_sent: int = 0     # posts sent to Discord during this check, to at least one channel (includes retries)
    turned_off: int = 0     # new posts not sent because their kind is switched off
    no_channel: int = 0     # new posts not sent because no Discord channel is chosen

    def as_dict(self):
        return asdict(self)


def item_kind(item) -> str:
    """post, repost or reply. Reposts carry a "reason"; replies have record.reply set."""
    reason = getattr(item, "reason", None)
    if reason is not None:
        reason_type = str(getattr(reason, "py_type", "") or type(reason).__name__).lower()
        if "pin" not in reason_type:  # a pinned post is the account's own post
            return REPOST
    record = getattr(getattr(item, "post", None), "record", None)
    return REPLY if getattr(record, "reply", None) else POST


def _new_session() -> SharedSession:
    return SharedSession()


def _post_url(handle: str, uri: str) -> str:
    # uri format: at://did:plc:xxx/app.bsky.feed.post/rkey
    rkey = uri.split("/")[-1]
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


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
    now = timezone.now()
    MonitoredAccount.objects.filter(pk=account.pk).update(
        last_checked_at=now, last_check_ok=True, last_check_category=None, last_success_at=now)
    account.last_check_ok, account.last_check_category = True, None
    if was_failing:
        activity.record(activity.CHECK_RECOVERED, ActivityEvent.SUCCESS,
                        f"Checking @{account.handle} works again.", account=account)


def _check_failed(account: MonitoredAccount, exc: Exception, technical: str = "") -> str:
    """Record a failed check of one account. Returns the problem category."""
    category = problems.classify_bluesky_error(exc)
    now = timezone.now()
    MonitoredAccount.objects.filter(pk=account.pk).update(
        last_checked_at=now, last_check_ok=False, last_check_category=category,
        last_error_at=now, last_error_category=category)
    account.last_check_ok, account.last_check_category = False, category
    activity.problem(activity.CHECK_FAILED, category,
                     f"Couldn't check @{account.handle}: {problems.get(category).title}.",
                     level=ActivityEvent.WARNING if problems.is_temporary(category) else ActivityEvent.ERROR,
                     account=account, exc=exc, technical=technical, merge_repeats=True)
    return category


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
def claim_post(account: MonitoredAccount, item, status: str, kind: str | None = None, targets=None) -> bool:
    """Record a post and, if it is to be sent, one delivery per channel (one transaction).

    Returns False if it was already recorded (by any process). `targets` (channels.targets_for)
    may be passed when already known. A post to be sent with nowhere to send it is recorded
    as skipped.
    """
    post = item.post
    kind = kind or item_kind(item)
    author = getattr(getattr(post, "author", None), "handle", None) or account.handle
    if status == SeenPost.PENDING:
        targets = channels.targets_for(account) if targets is None else targets
        if not targets:
            status = SeenPost.SKIPPED
    try:
        with transaction.atomic():
            seen = SeenPost.objects.create(
                account=account,
                post_uri=post.uri,
                post_cid=post.cid,
                text=getattr(post.record, "text", "") or "",
                post_url=_post_url(author, post.uri),
                status=status,
                kind=kind,
            )
            if status == SeenPost.PENDING:
                PostDelivery.objects.bulk_create(
                    PostDelivery(post=seen, channel=t.channel, role_id=t.role_id) for t in targets)
        return True
    except IntegrityError:
        return False


# -- delivering -----------------------------------------------------------------
def _due_for_delivery(account: MonitoredAccount, now):
    stale_before = now - timedelta(seconds=settings.SEND_STALE_SECONDS)
    return PostDelivery.objects.filter(post__account=account).filter(
        Q(status=PostDelivery.PENDING)
        | Q(status=PostDelivery.RETRY, next_attempt_at__lte=now)
        | Q(status=PostDelivery.SENDING, claimed_at__lt=stale_before)
    ).select_related("post", "channel").order_by("post_id", "id")


def refresh_post_status(post_id):
    """Sum up a post's deliveries on the post: pending while any is unsent, then sent,
    partly_sent, failed, or skipped (every delivery was cancelled). One UPDATE, so two
    processes finishing deliveries of the same post can't leave it out of date."""
    deliveries = PostDelivery.objects.filter(post=OuterRef("pk"))
    unsent = Exists(deliveries.filter(status__in=PostDelivery.UNFINISHED))
    sent = Exists(deliveries.filter(status=PostDelivery.SENT))
    failed = Exists(deliveries.filter(status=PostDelivery.FAILED))
    SeenPost.objects.filter(pk=post_id).exclude(status=SeenPost.SEEDED).filter(Exists(deliveries)).update(
        status=Case(
            When(unsent, then=Value(SeenPost.PENDING)),
            When(Q(sent) & Q(failed), then=Value(SeenPost.PARTLY_SENT)),
            When(sent, then=Value(SeenPost.SENT)),
            When(failed, then=Value(SeenPost.FAILED)),
            default=Value(SeenPost.SKIPPED)),
        sent_at=Subquery(deliveries.filter(status=PostDelivery.SENT).order_by("-sent_at").values("sent_at")[:1]),
    )


def _mention_for(delivery: PostDelivery, account: MonitoredAccount, post: SeenPost):
    """The role to mention for this delivery: (role_id, None), or ("", failure).

    Before a channel's first mention the bot asks Discord which server the webhook posts to
    (reading a webhook posts nothing). A Role ID equal to the server's ID would be @everyone,
    so that notification is sent without the mention, as is one whose server Discord didn't
    name. If Discord can't be asked, the delivery fails and is retried like a failed send.
    """
    role, channel = delivery.role_id, delivery.channel
    if not role:
        return "", None
    guild = channel.guild_id or ""
    if not guild:
        info = sender.lookup_webhook(channel.url, account.handle)
        if not info.ok:
            return "", info.failure
        guild = info.guild_id
        if guild:
            channels.remember_guild(channel.pk, guild)
            channel.guild_id = guild
    if not guild or mentions.is_server_id(role, guild):
        reason = ("it is the Discord server's own ID, which would mean @everyone" if guild
                  else "Discord didn't say which server the channel is in, so it couldn't be checked")
        activity.problem(activity.MENTION_NOT_SENT, "mention_refused",
                         f'A post from @{account.handle} was sent to "{channel.name}" without the role mention: '
                         f"{reason}.", level=ActivityEvent.WARNING, account=account, channel=channel,
                         detail=_post_detail(post, role_id=role), merge_repeats=True)
        return "", None
    return role, None


def deliver_pending(account: MonitoredAccount) -> int:
    """Send every notification that is due for this account.

    Returns how many posts were sent to at least one channel. Each channel goes on its own:
    when a send to one channel fails, that channel's later posts wait for the next delivery
    (so they keep their order) and the other channels carry on.
    """
    now = timezone.now()
    window_start = now - timedelta(hours=settings.SEND_RETRY_WINDOW_HOURS)
    failing, sent_posts, touched = set(), set(), set()
    for delivery in list(_due_for_delivery(account, now)):
        if shutdown.requested():
            logger.info("Stopping: the rest of %s's notifications wait for the next start", account.handle)
            break  # nothing new is claimed, so nothing is left half sent
        if delivery.channel_id in failing:
            continue
        post, channel = delivery.post, delivery.channel
        # The channel is part of the condition: a delivery moved to another channel (channel
        # deleted with "move accounts") is left to the next delivery instead of being sent here.
        snapshot = PostDelivery.objects.filter(pk=delivery.pk, status=delivery.status, channel_id=delivery.channel_id,
                                               claim_token=delivery.claim_token)

        if post.seen_at < window_start:
            if snapshot.update(status=PostDelivery.FAILED, next_attempt_at=None,
                               last_error=delivery.last_error or "Not sent within 24 hours.") == 1:
                touched.add(post.pk)
                activity.problem(activity.SEND_GAVE_UP, "send_expired",
                                 f'A post from @{account.handle} was never sent to "{channel.name}": '
                                 f"it couldn't be sent within {settings.SEND_RETRY_WINDOW_HOURS} hours.",
                                 account=account, channel=channel, attempt=delivery.attempts or None,
                                 detail=_post_detail(post), technical=delivery.last_error)
            continue

        token = uuid.uuid4().hex
        if snapshot.update(status=PostDelivery.SENDING, claim_token=token, claimed_at=timezone.now(),
                           attempts=F("attempts") + 1) != 1:
            continue  # another process claimed it between our read and our update
        touched.add(post.pk)

        role, result = _mention_for(delivery, account, post)
        if result is None:
            extra = {"kind": post.kind} if post.kind in (REPOST, REPLY) else {}  # plain posts: as before
            if role:
                extra["role_id"] = role
            result = sender.deliver_alert(
                webhook_url=channel.url,
                handle=account.handle,
                display_name=account.display_name or account.handle,
                text=post.text,
                post_url=post.post_url,
                **extra,
            )
        mine = PostDelivery.objects.filter(pk=delivery.pk, claim_token=token)
        channels.record_delivery(channel.pk, result.ok, result.error)
        attempts = delivery.attempts + 1
        max_attempts = settings.SEND_MAX_ATTEMPTS
        where = f'@{account.handle} to "{channel.name}"'
        if result.ok:
            mine.update(status=PostDelivery.SENT, sent_at=timezone.now(), next_attempt_at=None, last_error="")
            sent_posts.add(post.pk)
            logger.info("Sent post from %s to channel %s: %s", account.handle, channel.pk, post.post_uri)
            mention = " with the role mention" if role else ""
            retry_note = f" It worked on try {attempts} of {max_attempts}." if attempts > 1 else ""
            activity.record(activity.POST_SENT, ActivityEvent.SUCCESS, f"Sent a post from {where}{mention}.{retry_note}",
                            account=account, channel=channel, attempt=attempts,
                            detail=_post_detail(post, **({"role_id": role} if role else {})))
            continue

        failing.add(channel.pk)  # this channel's later posts wait, to keep their order
        category = result.category or "discord_error"
        if attempts >= max_attempts:
            mine.update(status=PostDelivery.FAILED, next_attempt_at=None, last_error=result.error[:500])
            logger.error("Gave up sending %s to channel %s after %d attempts", post.post_uri, channel.pk, attempts)
            activity.problem(activity.SEND_GAVE_UP, category,
                             f"Gave up sending a post from {where} after {attempts} tries. {result.error}",
                             account=account, channel=channel, attempt=attempts,
                             detail=_post_detail(post, http_status=result.status_code), technical=result.technical)
        else:
            delay = RETRY_DELAYS[min(attempts, len(RETRY_DELAYS)) - 1]
            if result.retry_after:
                delay = max(delay, timedelta(seconds=result.retry_after))
            next_at = timezone.now() + delay
            mine.update(status=PostDelivery.RETRY, next_attempt_at=next_at, last_error=result.error[:500])
            activity.problem(activity.SEND_FAILED, category,
                             f"Couldn't send a post from {where} (try {attempts} of {max_attempts}). "
                             f"{result.error} The bot will try again in about {_wait_text(delay)}.",
                             level=ActivityEvent.WARNING, account=account, channel=channel, attempt=attempts,
                             detail=_post_detail(post, http_status=result.status_code,
                                                 next_attempt_at=next_at.isoformat()),
                             technical=result.technical)
    for post_id in touched:
        refresh_post_status(post_id)
    return len(sent_posts)


# -- checking -------------------------------------------------------------------
def poll_account(account: MonitoredAccount, session=None) -> int:
    """Fetch recent posts for one account and send any new ones. Returns count of posts sent.

    The details of what happened are left on `account.check_report` (an AccountCheck).
    Raises AccountBusy if another process is checking this account right now, and
    BlueskyLoginError if the bot's own login doesn't work (nothing can be checked).
    """
    report = AccountCheck(account.pk, account.handle)
    account.check_report = report
    token = acquire_account_lock(account.pk)
    if token is None:
        raise AccountBusy(account.handle)
    try:
        account.refresh_from_db(fields=["first_poll_done", "is_active", "display_name", "last_check_ok",
                                        "announce_posts", "announce_reposts", "announce_replies"])
        # Bluesky trouble never blocks Discord retries of posts already claimed.
        try:
            items = fetch_new_items(session or _new_session(), account)
        except BlueskyLoginError:
            report.posts_sent = deliver_pending(account)
            raise
        except (BlueskyUnavailable, Exception) as exc:
            # Only the error type here; the full (scrubbed) cause is in the activity log entry.
            logger.error("Failed to fetch feed for %s: %s", account.handle, type(exc).__name__)
            report.result, report.category = FAILED, _check_failed(account, exc)
            report.posts_sent = deliver_pending(account)
            return report.posts_sent
        _check_ok(account)

        is_first_poll = not account.first_poll_done
        targets = channels.targets_for(account)
        for item in items:
            kind = item_kind(item)
            if is_first_poll:
                status = SeenPost.SEEDED
            elif not account.announces(kind):
                status = SeenPost.SKIPPED  # this kind is switched off for the account: noted, never sent
            elif targets:
                status = SeenPost.PENDING
            else:
                status = SeenPost.SKIPPED  # no channel set: noted, never sent (as before)
            if not claim_post(account, item, status, kind=kind, targets=targets):
                continue
            report.new_posts += 1
            if status == SeenPost.SKIPPED:
                if account.announces(kind):
                    report.no_channel += 1
                else:
                    report.turned_off += 1
        claimed = report.new_posts
        report.first_check = is_first_poll

        if is_first_poll:
            MonitoredAccount.objects.filter(pk=account.pk).update(
                first_poll_done=True, first_checked_at=timezone.now(), first_check_posts=claimed)
            account.first_poll_done = True
            logger.info("Seeded existing posts for %s - nothing sent on the first check", account.handle)
            activity.record(activity.FIRST_CHECK_DONE, ActivityEvent.INFO,
                            f"First check of @{account.handle} done. Noted {_plural(claimed, 'existing post')}; "
                            "none were sent to Discord. You'll get a notification for each new post from now on.",
                            account=account, detail={"seeded_posts": claimed})
        elif report.no_channel:
            activity.problem(activity.POST_NOT_SENT, "no_channel",
                             f"{_plural(report.no_channel, 'new post')} from @{account.handle} not sent: "
                             "no Discord channel is chosen for this account.",
                             level=ActivityEvent.WARNING, account=account, detail={"posts": report.no_channel},
                             merge_repeats=True)

        report.posts_sent = deliver_pending(account)
        return report.posts_sent
    finally:
        release_account_lock(account.pk, token)


def _unexpected_failure(account: MonitoredAccount, exc: Exception) -> AccountCheck:
    """An error outside reading the feed (a bug, a database hiccup). Recorded on this account
    only, so the remaining accounts are still checked."""
    logger.error("Unexpected error while checking %s: %s", account.handle, type(exc).__name__)
    report = AccountCheck(account.pk, account.handle, result=FAILED, category="internal")
    try:
        report.category = _check_failed(account, exc, technical="".join(traceback.format_exception(exc))[-1000:])
    except Exception:  # even recording it failed; the console log has the error type
        logger.exception("Couldn't record the failed check of %s", account.handle)
    return report


def check_accounts(accounts, *, should_continue=None, deadline=None, session=None, on_progress=None):
    """Check the given accounts one after another with one shared Bluesky session.

    Each account gets its own lock, so a check that is already running elsewhere (the
    scheduled check, or another "Check all") is reported as already_checking instead of
    being run twice. A failure in one account never stops the others; only a login that
    doesn't work stops the rest, because then nothing can be read.

    should_continue: checked before each account (the scheduler passes "do I still hold the lease?").
    deadline: a time.monotonic() value; accounts not reached by then are skipped (time_limit).
    on_progress: optional callable(account, report). Called with report=None just before an
    account is checked, and with its AccountCheck once it is done (used by Check All jobs).
    Returns (CycleSummary, [AccountCheck, ...]) in the order of `accounts`.
    """
    summary, reports = CycleSummary(), _Reports(on_progress)
    session = session or _new_session()
    stopped = None  # (reason, category) once the remaining accounts can't be checked
    for account in accounts:
        if stopped is None and shutdown.requested():
            logger.info("Stopping this check: the checker is shutting down")
            summary.result, stopped = "interrupted", ("stopped", "")
        if stopped is None and should_continue is not None and not should_continue():
            logger.warning("Stopping this check cycle: the checker lease was lost")
            summary.result, stopped = "interrupted", ("stopped", "")
        if stopped is None and deadline is not None and time.monotonic() > deadline:
            logger.warning("Stopping: the time limit for this check was reached")
            stopped = ("time_limit", "")
        if stopped is not None:
            reports.append(AccountCheck(account.pk, account.handle, result=SKIPPED,
                                        reason=stopped[0], category=stopped[1]))
            continue

        reports.starting(account)
        try:
            sent = poll_account(account, session=session)
        except AccountBusy:
            logger.info("Skipping %s: it is already being checked", account.handle)
            summary.accounts_busy += 1
            reports.append(AccountCheck(account.pk, account.handle, result=ALREADY_CHECKING))
            continue
        except BlueskyLoginError as exc:
            logger.error("Check cycle stopped: %s", exc)
            summary.category = getattr(exc, "category", "bluesky_login_rejected")
            summary.result = "not_configured" if summary.category == "bluesky_not_configured" else "failed"
            stopped = ("not_configured" if summary.result == "not_configured" else "login_failed", summary.category)
            reports.append(AccountCheck(account.pk, account.handle, result=SKIPPED,
                                        reason=stopped[0], category=stopped[1]))
            continue
        except Exception as exc:
            report = _unexpected_failure(account, exc)
            summary.accounts_checked += 1
            summary.accounts_failed += 1
            summary.category = report.category
            reports.append(report)
            continue

        summary.accounts_checked += 1
        summary.posts_sent += sent if isinstance(sent, int) else 0
        report = getattr(account, "check_report", None)
        if not isinstance(report, AccountCheck):  # poll_account replaced (tests)
            report = AccountCheck(account.pk, account.handle, posts_sent=sent if isinstance(sent, int) else 0)
        health = MonitoredAccount.objects.filter(pk=account.pk).values_list(
            "last_check_ok", "last_check_category").first()
        if health and health[0] is False:
            summary.accounts_failed += 1
            summary.category = health[1] or "internal"
            report.result, report.category = FAILED, summary.category
        reports.append(report)
    if summary.result == "ok" and summary.accounts_failed:
        summary.result = "failed" if summary.accounts_failed == summary.accounts_checked else "partial"
    return summary, list(reports)


class _Reports(list):
    """The list of AccountCheck results, telling on_progress about each one as it is added."""

    def __init__(self, on_progress):
        super().__init__()
        self.on_progress = on_progress

    def starting(self, account):
        self._tell(account, None)

    def append(self, report):
        super().append(report)
        self._tell(None, report)

    def _tell(self, account, report):
        if self.on_progress is None:
            return
        try:
            self.on_progress(account, report)
        except Exception:  # progress is only for display; never stop a check for it
            logger.exception("Couldn't record check progress")


def poll_all_active_accounts(should_continue=None) -> CycleSummary:
    """Poll every active monitored account with one shared Bluesky session. Called by the scheduler.

    should_continue: optional callable checked before each account (the scheduler passes
    "do I still hold the lease?"). Returns what the check did.
    """
    accounts = MonitoredAccount.objects.filter(is_active=True).order_by("id")
    summary, _reports = check_accounts(accounts, should_continue=should_continue)
    return summary
