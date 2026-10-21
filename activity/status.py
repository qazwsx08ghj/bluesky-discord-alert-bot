"""The bot's health, for GET /api/status/.

The answer comes from what the checker itself writes to the database (heartbeat, check
times, check results), never from the fact that this web request is being answered, so
"the control panel works but the checker is down" is reported as such.

Overall `state`, most serious first:
  unavailable     the checker isn't running: never started, stopped sending its heartbeat,
                  or its checks are overdue/stuck
  not_configured  setup isn't finished: no Bluesky login, no Discord channel, or no accounts
  problem         running, but something needs attention or is failing for now
  running         running normally
"""
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from . import problems
from .models import ActivityEvent

UNAVAILABLE, NOT_CONFIGURED, PROBLEM, RUNNING = "unavailable", "not_configured", "problem", "running"
RECENT_ERRORS = 5
RECENT_WINDOW = timedelta(hours=24)

HEADLINES = {
    UNAVAILABLE: "The checker isn't running",
    NOT_CONFIGURED: "Setup isn't finished",
    PROBLEM: "Running, with problems",
    RUNNING: "Running normally",
}

LAST_CHECK_RESULTS = {
    "ok": "Every account was checked.",
    "partial": "Some accounts couldn't be checked.",
    "failed": "The check didn't complete.",
    "not_configured": "Nothing was checked: the Bluesky login isn't set up.",
    "interrupted": "The check was handed over to another checker part-way.",
}


def _iso(value):
    return value.isoformat() if value else None


def _ago_text(seconds):
    minutes = int(seconds // 60)
    if minutes < 1:
        return "less than a minute ago"
    if minutes < 120:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    return f"{minutes // 60} hours ago"


def _problem(code, message, *, severity="error", since=None, account=None, channel=None):
    item = problems.describe(code)
    item.update(code=code, severity=severity, message=message, since=_iso(since))
    if account is not None:
        item["account"] = {"id": account.pk, "handle": account.handle}
    if channel is not None:
        item["channel"] = {"id": channel.pk, "name": channel.name}
    return item


# -- the checker ----------------------------------------------------------------------------
def scheduler_health(now, interval_minutes):
    from bluesky_monitor.models import SchedulerState

    state = SchedulerState.objects.filter(pk=1).first()
    lease_seconds = settings.SCHEDULER_LEASE_SECONDS
    stall = timedelta(minutes=settings.SCHEDULER_STALL_MINUTES)
    grace = timedelta(seconds=settings.SCHEDULER_OVERDUE_GRACE_SECONDS)

    heartbeat = state.heartbeat_at if state else None
    age = (now - heartbeat).total_seconds() if heartbeat else None
    started = state.cycle_started_at if state else None
    finished = state.cycle_finished_at if state else None
    checking_now = bool(started and (finished is None or finished < started))
    summary = (state.last_cycle_summary or None) if state else None

    if heartbeat is None:
        health, code, message = "not_started", "scheduler_not_started", "The checker hasn't started yet."
    elif age > lease_seconds:
        health, code = "stopped", "scheduler_stopped"
        message = f"The checker stopped responding {_ago_text(age)}."
    elif checking_now and now - started > stall:
        health, code = "stalled", "scheduler_stalled"
        message = f"A check has been running for {int((now - started).total_seconds() // 60)} minutes."
    elif _overdue(state, now, interval_minutes, grace):
        health, code, message = "stalled", "scheduler_stalled", "The next check is overdue."
    else:
        health, code = ("checking" if checking_now else "running"), ""
        message = "Checking accounts right now." if checking_now else "The checker is running."

    last_check = None
    if finished:
        result = (summary or {}).get("result")
        category = (summary or {}).get("category") or ""
        last_check = {
            "started_at": _iso(started if not checking_now else None),
            "finished_at": _iso(finished),
            "result": result,
            "message": LAST_CHECK_RESULTS.get(result, ""),
            "problem": problems.describe(category) if result not in (None, "ok", "interrupted") else None,
            "accounts_checked": (summary or {}).get("accounts_checked"),
            "accounts_failed": (summary or {}).get("accounts_failed"),
            "posts_sent": (summary or {}).get("posts_sent"),
        }
    return {
        "state": health,
        "healthy": health in ("running", "checking"),
        "message": message,
        "problem": problems.describe(code) if code else None,
        "heartbeat_at": _iso(heartbeat),
        "heartbeat_age_seconds": int(age) if age is not None else None,
        "active_since": _iso(state.started_at if state else None),
        "check_interval_minutes": interval_minutes,
        "checking_now": checking_now,
        "current_check_started_at": _iso(started if checking_now else None),
        "last_check": last_check,
        "next_check_at": _iso(state.next_run_at if state and health in ("running", "checking") else None),
    }


def _overdue(state, now, interval_minutes, grace):
    """No check started when one should have."""
    if state.next_run_at is not None:
        return now > state.next_run_at + grace
    # The planned time isn't known yet (first heartbeat pending): go by the last start.
    last_start = max(filter(None, [state.cycle_started_at, state.started_at]), default=None)
    return last_start is not None and now - last_start > timedelta(minutes=interval_minutes) + grace


# -- Bluesky login ---------------------------------------------------------------------------
def bluesky_login(now):
    from bluesky_monitor.bluesky import _fingerprint
    from bluesky_monitor.models import BlueskySessionState
    from botconfig import store

    handle, password = store.credentials()
    if not handle or not password:
        return {"state": "not_configured", "handle": handle or "", "problem": problems.describe("bluesky_not_configured"),
                "retry_after": None}
    state = BlueskySessionState.objects.filter(pk=1).first()
    current = state is not None and state.credential_fingerprint == _fingerprint(handle, password)
    result = {"state": "unknown", "handle": handle, "problem": None, "retry_after": None}
    if not current:
        return result  # not tried with these credentials yet
    waiting = state.login_retry_after and state.login_retry_after > now
    if state.login_failures:
        result.update(state="failed", problem=problems.describe("bluesky_login_rejected"))
    elif waiting:
        result.update(state="rate_limited", problem=problems.describe("bluesky_rate_limited"))
    elif state.session_string:
        result["state"] = "ok"
    if waiting:
        result["retry_after"] = _iso(state.login_retry_after)
    return result


# -- putting it together ---------------------------------------------------------------------
def compute(now=None):
    from bluesky_monitor.models import MonitoredAccount, SeenPost
    from botconfig import store
    from discord_alerts import channels
    from discord_alerts.models import DiscordWebhook

    now = now or timezone.now()
    interval = store.poll_interval_minutes()
    checker = scheduler_health(now, interval)
    login = bluesky_login(now)
    default = channels.default_channel()

    accounts = MonitoredAccount.objects.select_related("channel")
    active = [a for a in accounts.filter(is_active=True).order_by("id")]
    queue = dict(SeenPost.objects.filter(status__in=[SeenPost.PENDING, SeenPost.SENDING, SeenPost.RETRY])
                 .values_list("status").annotate(n=Count("id")).order_by())
    since = now - RECENT_WINDOW
    counts = {
        "accounts_total": accounts.count(),
        "accounts_active": len(active),
        "channels": DiscordWebhook.objects.count(),
        "pending_deliveries": sum(queue.values()),
        "deliveries_waiting": queue.get(SeenPost.PENDING, 0) + queue.get(SeenPost.SENDING, 0),
        "deliveries_retrying": queue.get(SeenPost.RETRY, 0),
        "sent_last_24h": SeenPost.objects.filter(status=SeenPost.SENT, sent_at__gte=since).count(),
        "failed_sends_last_24h": ActivityEvent.objects.filter(type="send_gave_up", created_at__gte=since).count(),
    }

    setup = {
        "bluesky_login": login["state"] != "not_configured",
        "discord_channel": counts["channels"] > 0,
        "accounts": counts["accounts_total"] > 0,
    }
    setup["complete"] = all(setup.values())
    missing = []
    if not setup["bluesky_login"]:
        missing.append(_problem("bluesky_not_configured", "Add the bot's Bluesky login in Settings.", severity="setup"))
    if not setup["discord_channel"]:
        missing.append(_problem("no_channel", "Add a Discord channel so the bot has somewhere to post.",
                                severity="setup"))
    if not setup["accounts"]:
        missing.append({"code": "no_accounts", "category": "no_accounts", "severity": "setup",
                        "title": "No accounts watched yet", "fix": "add_account", "since": None,
                        "explanation": "Add a Bluesky account to watch. Its new posts will be sent to Discord.",
                        "message": "Add a Bluesky account to watch."})

    current = _current_problems(now, checker, login, active, default)

    if not checker["healthy"]:
        state, message = UNAVAILABLE, checker["message"] + " " + checker["problem"]["explanation"]
    elif not setup["complete"]:
        state, message = NOT_CONFIGURED, " ".join(p["message"] for p in missing)
    elif current:
        state = PROBLEM
        first = current[0]
        others = len(current) - 1
        message = first["message"] + (f" (and {others} more problem{'s' if others != 1 else ''})" if others else "")
    else:
        state = RUNNING
        if not active:
            message = "Running. All watched accounts are paused, so nothing is being checked."
        else:
            message = (f"Checking {len(active)} account{'s' if len(active) != 1 else ''} "
                       f"every {interval} minute{'s' if interval != 1 else ''}.")

    return {
        "state": state,
        "headline": HEADLINES[state],
        "message": message,
        "checked_at": _iso(now),
        "scheduler": checker,
        "bluesky_login": login,
        "setup": {**setup, "missing": missing},
        "counts": counts,
        "problems": current,
        "recent_errors": _recent_errors(since),
    }


def _current_problems(now, checker, login, active, default):
    """Problems as things stand now (not a count of log lines): they clear once fixed."""
    from bluesky_monitor.models import SeenPost
    from discord_alerts.models import DiscordWebhook

    found = []
    if login["problem"] and login["state"] in ("failed", "rate_limited"):
        code = login["problem"]["category"]
        found.append(_problem(code, login["problem"]["title"] + ".",
                              severity="error" if login["state"] == "failed" else "warning"))

    last = checker.get("last_check") or {}
    if last.get("result") == "failed" and (last.get("problem") or {}).get("category") == "internal":
        found.append(_problem("internal", "The last check stopped because of an unexpected error.",
                              since=_parse(last.get("finished_at"))))

    for account in active:
        if account.last_check_ok is False:
            code = account.last_check_category if problems.get(account.last_check_category) else "internal"
            temporary = problems.is_temporary(code)
            found.append(_problem(code, f"@{account.handle}: {problems.get(code).title}.",
                                  severity="warning" if temporary else "error",
                                  since=account.last_checked_at, account=account))
        if account.channel_id is None and default is None:
            found.append(_problem("no_channel", f"@{account.handle}: no Discord channel chosen, so its new posts "
                                  "aren't sent.", severity="error", account=account))

    in_use = DiscordWebhook.objects.filter(
        Q(accounts__is_active=True) | Q(is_default=True)).distinct().filter(last_delivery_ok=False)
    for channel in in_use:
        code = _latest_send_category(channel.pk) or "discord_error"
        found.append(_problem(code, f'"{channel.name}": {problems.get(code).title}.',
                              severity="warning" if problems.is_temporary(code) else "error",
                              since=channel.last_delivery_at, channel=channel))

    retrying = SeenPost.objects.filter(status=SeenPost.RETRY).count()
    if retrying and not any(p.get("channel") for p in found):
        found.append({"code": "deliveries_retrying", "category": "discord_unavailable", "severity": "warning",
                      "title": "Some posts are waiting to be sent again",
                      "explanation": problems.get("discord_unavailable").explanation, "fix": "wait", "since": None,
                      "message": f"{retrying} post{'s are' if retrying != 1 else ' is'} waiting to be sent again."})
    order = {"error": 0, "warning": 1}
    return sorted(found, key=lambda p: order.get(p["severity"], 2))


def _latest_send_category(channel_id):
    event = (ActivityEvent.objects
             .filter(channel_id=channel_id, type__in=["send_failed", "send_gave_up", "channel_test_failed"])
             .order_by("-id").values_list("category", flat=True).first())
    return event if problems.get(event) else None


def _parse(value):
    from django.utils.dateparse import parse_datetime

    return parse_datetime(value) if value else None


def _recent_errors(since):
    from .serializers import ActivityEventSerializer

    events = (ActivityEvent.objects.filter(level__in=[ActivityEvent.ERROR, ActivityEvent.WARNING])
              .filter(Q(created_at__gte=since) | Q(last_seen_at__gte=since)).order_by("-id")[:RECENT_ERRORS])
    return ActivityEventSerializer(events, many=True).data
