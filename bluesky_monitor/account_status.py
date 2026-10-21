"""One account's status, as the control panel shows it (GET /api/accounts/ and friends).

`state`, first match wins:
  checking             a check of this account is running right now (its lock is held)
  paused               the user switched it off; it isn't checked
  error                its latest check failed (see `problem`; severity "warning" means it
                       usually clears by itself)
  waiting_first_check  added, but not checked yet. The first check notes existing posts
                       without sending them.
  active               checked normally

Times are returned as timestamps, never as "3 minutes ago" text, so they don't go stale
between refreshes; the frontend formats them.
Everything comes from fields on the account row, so a list needs no extra queries.
"""
from django.utils import timezone

from activity import problems

CHECKING, PAUSED, ERROR, WAITING, ACTIVE = "checking", "paused", "error", "waiting_first_check", "active"

LABELS = {
    CHECKING: "Checking now",
    PAUSED: "Paused",
    ERROR: "Problem",
    WAITING: "Waiting for first check",
    ACTIVE: "Active",
}


def _iso(value):
    return value.isoformat() if value else None


def _problem(code):
    code = code if problems.get(code) else "internal"
    item = problems.describe(code)
    item["severity"] = "warning" if problems.is_temporary(code) else "error"
    return item


def describe(account, now=None) -> dict:
    now = now or timezone.now()
    checking = bool(account.check_lock_until and account.check_lock_until > now)
    failing = account.last_check_ok is False
    first_done = bool(account.first_poll_done)

    if checking:
        state = CHECKING
        message = "Checking now. This is its first check." if not first_done else "Checking now."
    elif not account.is_active:
        state, message = PAUSED, "Paused. New posts aren't checked or sent until you turn it back on."
    elif failing:
        state = ERROR
        message = f"{_problem(account.last_check_category)['title']}."
    elif not first_done:
        state = WAITING
        message = ("Waiting for its first check. The first check notes the posts that already exist without "
                   "sending them, so nothing old is announced.")
    else:
        state, message = ACTIVE, "Watching for new posts."

    last_error = None
    if account.last_error_at or failing:
        category = account.last_error_category or account.last_check_category
        last_error = {"at": _iso(account.last_error_at or account.last_checked_at), "ongoing": failing,
                      **_problem(category)}

    return {
        "state": state,
        "state_label": LABELS[state],
        "state_message": message,
        "checking_now": checking,
        "problem": _problem(account.last_check_category) if failing else None,
        "first_check": {
            "done": first_done,
            "at": _iso(account.first_checked_at),
            "seeded_posts": account.first_check_posts,  # null for accounts first checked before step 6
        },
        "last_checked_at": _iso(account.last_checked_at),
        "last_check_ok": account.last_check_ok,
        "last_success_at": _iso(account.last_success_at),
        "last_error": last_error,
    }
