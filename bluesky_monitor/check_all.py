""""Check all": check every account now, and say what happened to each.

This runs in the background: POST /api/accounts/check_all/ queues a CheckJob
and the checker runs it (see jobs.py), which calls run() below.

run() uses poller.check_accounts(), exactly like the scheduled check: the same per-account
lock, claim-before-send and retry rules. So if the scheduled check (or a second click) is
already checking an account, that account is reported as `already_checking` and nothing is
sent twice. One account failing never stops the others.

Paused accounts are listed as skipped, so the panel can show every account's row.
The run stops starting new accounts after CHECK_ALL_TIME_LIMIT_SECONDS; any not reached
are reported as skipped (time_limit) and are checked by the next scheduled check.
"""
import time

from django.conf import settings
from django.utils import timezone

from activity import problems
from . import account_status
from .models import MonitoredAccount
from .poller import ALREADY_CHECKING, CHECKED, FAILED, SKIPPED, AccountCheck, check_accounts


def _plural(n, word, plural=None):
    return f"{n} {word}" if n == 1 else f"{n} {plural or word + 's'}"


SKIP_MESSAGES = {
    "paused": "Paused, so it wasn't checked.",
    "login_failed": "Not checked: Bluesky didn't accept the bot's login.",
    "not_configured": "Not checked: the bot's Bluesky login isn't set up yet.",
    "time_limit": "Not checked this time because the check took too long. The next scheduled check includes it.",
    "stopped": "Not checked: the check was stopped.",
}


def account_message(report: AccountCheck) -> str:
    if report.result == ALREADY_CHECKING:
        return "Already being checked right now (by the scheduled check or another request). Its result shows shortly."
    if report.result == SKIPPED:
        return SKIP_MESSAGES.get(report.reason, "Not checked.")
    if report.result == FAILED:
        title = (problems.get(report.category) or problems.get("internal")).title
        sent = f" {_plural(report.posts_sent, 'waiting post')} sent anyway." if report.posts_sent else ""
        return f"Couldn't check: {title}.{sent}"
    if report.first_check:
        return (f"First check done. Noted {_plural(report.new_posts, 'existing post')}; none were sent. "
                "You'll get a notification for each new post from now on.")
    if not report.new_posts and not report.posts_sent:
        return "No new posts."
    parts = [f"Found {_plural(report.new_posts, 'new post')}." if report.new_posts else "No new posts."]
    if report.posts_sent:
        parts.append(f"Sent {report.posts_sent} to Discord.")
    if report.turned_off:
        parts.append(f"{report.turned_off} not sent (notifications for that kind of post are off for this account).")
    if report.no_channel:
        parts.append(f"{report.no_channel} not sent: no Discord channel is chosen.")
    waiting = report.new_posts - report.turned_off - report.no_channel - report.posts_sent
    if waiting > 0:
        parts.append(f"{waiting} waiting to be sent; the bot retries automatically.")
    return " ".join(parts)


# Rows of accounts that aren't done yet (shown while a Check All job runs).
WAITING, CHECKING = "waiting", "checking"
PENDING_MESSAGES = {WAITING: "Waiting to be checked.", CHECKING: "Being checked now."}


def _pending_row(account, result):
    return {"id": account.pk, "handle": account.handle, "result": result, "reason": None,
            "message": PENDING_MESSAGES[result], "problem": None, "first_check": None,
            "new_posts": 0, "posts_sent": 0, "not_announced": 0, "state": None, "state_label": None}


def _account_row(report: AccountCheck, account, now):
    status = account_status.describe(account, now) if account is not None else {}
    return {
        "id": report.account_id,
        "handle": report.handle,
        "result": report.result,
        "reason": report.reason or None,
        "message": account_message(report),
        "problem": problems.describe(report.category) if report.category else None,
        "first_check": report.first_check,
        "new_posts": report.new_posts,
        "posts_sent": report.posts_sent,
        "not_announced": report.turned_off + report.no_channel,
        "state": status.get("state"),
        "state_label": status.get("state_label"),
    }


def run(time_limit_seconds=None, *, should_continue=None, progress=None) -> dict:
    """Check every active account now. Returns the result (the job's final body).

    should_continue: passed to check_accounts (the job passes "does the checker still hold the lease?").
    progress: optional callable(rows, done, total, current_account) called whenever a row
    changes, with every account's row in order (see _pending_row for rows not done yet).
    """
    started = timezone.now()
    limit = settings.CHECK_ALL_TIME_LIMIT_SECONDS if time_limit_seconds is None else time_limit_seconds
    accounts = list(MonitoredAccount.objects.order_by("id"))
    active = [a for a in accounts if a.is_active]

    on_progress = _Progress(accounts, active, progress) if progress is not None else None
    _summary, reports = (check_accounts(active, deadline=time.monotonic() + limit, should_continue=should_continue,
                                        on_progress=on_progress) if active else (None, []))
    reports += [AccountCheck(a.pk, a.handle, result=SKIPPED, reason="paused") for a in accounts if not a.is_active]
    order = {a.pk: i for i, a in enumerate(accounts)}
    reports.sort(key=lambda r: order.get(r.account_id, 0))

    fresh = {a.pk: a for a in MonitoredAccount.objects.filter(pk__in=[r.account_id for r in reports])}
    now = timezone.now()
    rows = [_account_row(r, fresh.get(r.account_id), now) for r in reports]
    counts = {
        "checked": sum(r.result == CHECKED for r in reports),
        "failed": sum(r.result == FAILED for r in reports),
        "skipped": sum(r.result == SKIPPED for r in reports),
        "already_checking": sum(r.result == ALREADY_CHECKING for r in reports),
        "new_posts": sum(r.new_posts for r in reports if not r.first_check),
        "posts_sent": sum(r.posts_sent for r in reports),
    }
    result, message, problem = _overall(accounts, active, reports, counts)
    return {
        "result": result,
        "message": message,
        "problem": problem,
        "started_at": started.isoformat(),
        "finished_at": now.isoformat(),
        "counts": counts,
        "accounts": rows,
    }


class _Progress:
    """Keeps every account's row up to date while the check runs, and hands them to `progress`."""

    def __init__(self, accounts, active, progress):
        self.progress, self.total, self.done = progress, len(active), 0
        self.rows = {a.pk: (_pending_row(a, WAITING) if a.is_active else
                            _account_row(AccountCheck(a.pk, a.handle, result=SKIPPED, reason="paused"), a, None))
                     for a in accounts}
        self._send(None)

    def __call__(self, account, report):
        if report is None:  # starting this account
            self.rows[account.pk] = _pending_row(account, CHECKING)
            self._send(account)
            return
        fresh = MonitoredAccount.objects.filter(pk=report.account_id).first()
        self.rows[report.account_id] = _account_row(report, fresh, timezone.now())
        self.done += 1
        self._send(None)

    def _send(self, current):
        self.progress(list(self.rows.values()), self.done, self.total, current)


def _overall(accounts, active, reports, counts):
    """(result, message, problem) for the whole run.

    result: ok | partial | failed | not_configured | login_failed | nothing_to_check
    """
    if not accounts:
        return "nothing_to_check", "No accounts to check yet. Add a Bluesky account first.", None
    if not active:
        return "nothing_to_check", "All accounts are paused, so nothing was checked.", None

    done = counts["checked"] + counts["failed"]
    login_stop = next((r for r in reports if r.reason in ("login_failed", "not_configured")), None)
    if login_stop is not None and not done:
        if login_stop.reason == "not_configured":
            return ("not_configured", "Nothing was checked: the bot's Bluesky login isn't set up yet. "
                    "Add it in Settings.", problems.describe("bluesky_not_configured"))
        category = login_stop.category or "bluesky_login_rejected"
        return ("login_failed", f"Nothing was checked: {problems.get(category).title}.",
                problems.describe(category))

    parts = [f"Checked {_plural(done, 'account')}."]
    if counts["posts_sent"]:
        parts.append(f"Sent {_plural(counts['posts_sent'], 'post')} to Discord.")
    elif done and not counts["new_posts"]:
        parts.append("No new posts.")
    if counts["failed"]:
        parts.append(f"{_plural(counts['failed'], 'account')} couldn't be checked.")
    if counts["already_checking"]:
        parts.append(f"{_plural(counts['already_checking'], 'account was', 'accounts were')} already being checked.")
    not_reached = sum(1 for r in reports if r.reason in ("time_limit", "stopped"))
    if not_reached:
        parts.append(f"{_plural(not_reached, 'account')} not reached; the next scheduled check includes "
                     f"{'it' if not_reached == 1 else 'them'}.")
    failed_category = next((r.category for r in reports if r.result == FAILED and r.category), "")
    if login_stop is not None:
        # The login stopped working part-way through.
        category = login_stop.category or "bluesky_login_rejected"
        stopped = sum(1 for r in reports if r.reason == login_stop.reason)
        parts.append(f"Then the check stopped ({problems.get(category).title}), so "
                     f"{_plural(stopped, 'account was', 'accounts were')} not checked.")
        return "partial", " ".join(parts), problems.describe(category)
    if counts["failed"]:
        return ("failed" if counts["failed"] == done else "partial", " ".join(parts),
                problems.describe(failed_category) if failed_category else None)
    return "ok", " ".join(parts), None
