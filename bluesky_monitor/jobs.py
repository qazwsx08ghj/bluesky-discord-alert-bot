"""Background "Check all" jobs.

    POST /api/accounts/check_all/   -> 202, a job (status queued) and its URL
    GET  /api/check-jobs/{id}/      -> progress, then the per-account results
    GET  /api/check-jobs/latest/    -> the newest job (to show again after a page refresh)

How it runs, with no extra services (no Celery, no Redis):
- The web request only adds a CheckJob row (status queued). If a Check All is already
  queued or running, that job is returned instead of starting a second one.
- The checker process (scheduler.py) looks for queued jobs every CHECK_JOB_POLL_SECONDS
  in a small background thread. Only the checker holding the scheduler lease runs
  jobs, and it claims a job with one conditional UPDATE, so a job runs exactly once.
- The job calls check_all.run(), which uses the same per-account locks and post claims
  as the scheduled check: an account being checked elsewhere is reported as
  already_checking, and no post is ever sent twice.
- Progress and each account's result are written to the row as they happen, so a page
  refresh, or another browser, shows the same state.

Recovery:
- A running job whose checker stopped (its lease expired or moved to another checker)
  becomes `interrupted`; accounts it didn't reach are listed as not checked.
- A queued job that no checker picks up within CHECK_JOB_QUEUE_TIMEOUT_SECONDS
  becomes `expired`, with a message saying the checker isn't running.
- Only the newest CHECK_JOBS_KEPT finished jobs are kept.
"""
import logging
import traceback
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from activity import log as activity, problems
from . import check_all, locks
from .models import CheckJob, SchedulerState

logger = logging.getLogger(__name__)

KIND = "check_all"

INTERRUPTED_MESSAGE = ("The check stopped before it finished, because the bot was restarted or stopped. "
                       "Accounts it didn't reach are checked at the next scheduled check.")
EXPIRED_MESSAGE = ("The check didn't start because the bot's checker isn't running. "
                   "Start the bot, then press Check all again.")
NOT_REACHED_MESSAGE = "Not checked: the check stopped before reaching it. The next scheduled check includes it."
FAILED_MESSAGE = ("The check stopped because of an unexpected error. Accounts it didn't reach are checked "
                  "at the next scheduled check.")

RESULT_LABELS = {
    "ok": "Done",
    "partial": "Partly done",
    "failed": "Failed",
    "nothing_to_check": "Nothing to check",
    "not_configured": "Login not set up",
    "login_failed": "Login problem",
}


# -- asking for a check ---------------------------------------------------------------------------
def request_check_all() -> tuple[CheckJob, bool]:
    """Queue a Check All, or return the one already queued or running. Returns (job, created)."""
    tidy()
    existing = CheckJob.objects.filter(kind=KIND, status__in=CheckJob.ACTIVE).order_by("requested_at").first()
    if existing is not None:
        return existing, False
    job = CheckJob.objects.create(kind=KIND)
    prune()
    return job, True


def checker_running(now=None) -> bool:
    """True while some checker holds a valid scheduler lease (so queued jobs will start)."""
    now = now or timezone.now()
    return SchedulerState.objects.filter(pk=1, lease_expires_at__gt=now).exclude(instance_id="").exists()


# -- running jobs (in the checker) ----------------------------------------------------------------
def run_pending(instance_id: str) -> int:
    """Run queued jobs, oldest first, if this checker holds the lease. Returns how many ran."""
    close_old_connections()
    tidy()
    ran = 0
    while CheckJob.objects.filter(status=CheckJob.QUEUED).exists() and locks.holds_scheduler_lease(instance_id):
        job = claim_next(instance_id)
        if job is None:
            break
        execute(job, instance_id)
        ran += 1
    return ran


def claim_next(instance_id: str):
    """Take the oldest queued job. One conditional UPDATE, so two checkers can't both take it."""
    job = CheckJob.objects.filter(status=CheckJob.QUEUED).order_by("requested_at").first()
    if job is None:
        return None
    now = timezone.now()
    taken = CheckJob.objects.filter(pk=job.pk, status=CheckJob.QUEUED).update(
        status=CheckJob.RUNNING, worker=instance_id, started_at=now, updated_at=now)
    return CheckJob.objects.get(pk=job.pk) if taken else None


def execute(job: CheckJob, instance_id: str) -> None:
    """Run one claimed job to the end, writing progress as each account is done."""
    mine = CheckJob.objects.filter(pk=job.pk, status=CheckJob.RUNNING, worker=instance_id)

    def progress(rows, done, total, current):
        mine.update(accounts=rows, done=done, total=total, updated_at=timezone.now(),
                    current_account_id=current.pk if current is not None else None,
                    current_handle=current.handle if current is not None else "")

    logger.info("Check all %s started", job.pk)
    try:
        body = check_all.run(should_continue=lambda: locks.holds_scheduler_lease(instance_id), progress=progress)
    except Exception as exc:  # keep the checker alive; the job says what happened
        logger.exception("Check all %s failed", job.pk)
        activity.problem(activity.CYCLE_FAILED, "internal", FAILED_MESSAGE,
                         technical="".join(traceback.format_exception(exc))[-1000:], merge_repeats=True)
        current = CheckJob.objects.filter(pk=job.pk).first()
        now = timezone.now()
        mine.update(status=CheckJob.FINISHED, finished_at=now, updated_at=now, result="failed",
                    message=FAILED_MESSAGE, problem_category="internal", current_account_id=None,
                    current_handle="", accounts=_close_rows(current.accounts if current else []))
        return

    now = timezone.now()
    finished = mine.update(
        status=CheckJob.FINISHED, finished_at=now, updated_at=now, result=body["result"],
        message=body["message"], problem_category=(body["problem"] or {}).get("category", ""),
        counts=body["counts"], accounts=body["accounts"], current_account_id=None, current_handle="",
        done=sum(1 for r in body["accounts"] if r["reason"] != "paused"),
        total=sum(1 for r in body["accounts"] if r["reason"] != "paused"),
    )
    if not finished:
        logger.warning("Check all %s finished after it was marked interrupted; its result wasn't saved", job.pk)
    logger.info("Check all %s finished: %s", job.pk, body["result"])


# -- housekeeping ---------------------------------------------------------------------------------
def tidy(now=None) -> None:
    """Expire queued jobs nobody picked up, and mark jobs of a stopped checker as interrupted.

    Called before reading or running jobs, so the answer is right even if no checker is running.
    Reads first and writes only when something needs changing.
    """
    now = now or timezone.now()
    expire_before = now - timedelta(seconds=settings.CHECK_JOB_QUEUE_TIMEOUT_SECONDS)
    stale = CheckJob.objects.filter(status=CheckJob.QUEUED, requested_at__lt=expire_before)
    if stale.exists():
        stale.update(status=CheckJob.EXPIRED, finished_at=now, updated_at=now, message=EXPIRED_MESSAGE)

    running = list(CheckJob.objects.filter(status=CheckJob.RUNNING))
    if not running:
        return
    holder = SchedulerState.objects.filter(pk=1).values("instance_id", "lease_expires_at").first() or {}
    too_long = timedelta(seconds=settings.CHECK_ALL_TIME_LIMIT_SECONDS + settings.ACCOUNT_LOCK_SECONDS)
    for job in running:
        alive = (holder.get("instance_id") == job.worker and holder.get("lease_expires_at") is not None
                 and holder["lease_expires_at"] > now)
        if alive and (job.started_at is None or now - job.started_at < too_long):
            continue
        CheckJob.objects.filter(pk=job.pk, status=CheckJob.RUNNING, worker=job.worker).update(
            status=CheckJob.INTERRUPTED, finished_at=now, updated_at=now, message=INTERRUPTED_MESSAGE,
            current_account_id=None, current_handle="", accounts=_close_rows(job.accounts))
        logger.warning("Check all %s was interrupted (its checker stopped)", job.pk)


def _close_rows(rows):
    """Rows not done yet become 'not checked' (skipped, reason stopped)."""
    closed = []
    for row in rows or []:
        if row.get("result") in (check_all.WAITING, check_all.CHECKING):
            row = {**row, "result": "skipped", "reason": "stopped", "message": NOT_REACHED_MESSAGE}
        closed.append(row)
    return closed


def prune() -> None:
    """Keep only the newest CHECK_JOBS_KEPT finished jobs."""
    old = (CheckJob.objects.exclude(status__in=CheckJob.ACTIVE).order_by("-requested_at")
           .values_list("pk", flat=True)[settings.CHECK_JOBS_KEPT:])
    ids = list(old)
    if ids:
        CheckJob.objects.filter(pk__in=ids).delete()


# -- what the API returns -------------------------------------------------------------------------
def counts_from_rows(rows):
    """Counts for the rows so far (while running; a finished job keeps the counts of its run)."""
    return {
        "checked": sum(r.get("result") == "checked" for r in rows),
        "failed": sum(r.get("result") == "failed" for r in rows),
        "skipped": sum(r.get("result") == "skipped" for r in rows),
        "already_checking": sum(r.get("result") == "already_checking" for r in rows),
        "waiting": sum(r.get("result") in (check_all.WAITING, check_all.CHECKING) for r in rows),
        "new_posts": sum(r.get("new_posts") or 0 for r in rows if not r.get("first_check")),
        "posts_sent": sum(r.get("posts_sent") or 0 for r in rows),
    }


def url(job) -> str:
    return f"/api/check-jobs/{job.pk}/"


def describe(job: CheckJob, *, checker=None, now=None) -> dict:
    """A job as the API returns it. `checker`: checker_running(), if already known."""
    now = now or timezone.now()
    final = job.status not in CheckJob.ACTIVE
    checker = checker_running(now) if checker is None and not final else checker
    rows = job.accounts or []
    total, done = job.total, job.done

    if job.status == CheckJob.QUEUED:
        label = "Waiting to start"
        message = ("The check starts in a moment." if checker else
                   "Waiting for the bot's checker, which isn't running right now. The check starts as soon as "
                   "it is. If it hasn't started within a couple of minutes, it is cancelled.")
    elif job.status == CheckJob.RUNNING:
        label = "Checking"
        message = (f"Checking @{job.current_handle}… ({done} of {total} done)" if job.current_handle
                   else f"{done} of {total} accounts done.")
    elif job.status == CheckJob.FINISHED:
        label, message = RESULT_LABELS.get(job.result, "Done"), job.message
    elif job.status == CheckJob.INTERRUPTED:
        label, message = "Stopped", job.message or INTERRUPTED_MESSAGE
    else:
        label, message = "Didn't start", job.message or EXPIRED_MESSAGE

    counts = job.counts if job.status == CheckJob.FINISHED and job.counts else counts_from_rows(rows)
    counts.setdefault("waiting", 0)
    return {
        "id": str(job.pk),
        "kind": job.kind,
        "status": job.status,
        "final": final,
        "status_label": label,
        "message": message,
        "result": job.result or None,
        "problem": problems.describe(job.problem_category) if job.problem_category else None,
        "requested_at": job.requested_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "updated_at": job.updated_at,
        "progress": {
            "total": total,
            "done": done,
            "percent": (round(100 * done / total) if total else (100 if final else 0)),
            "current": ({"id": job.current_account_id, "handle": job.current_handle}
                        if job.current_handle else None),
        },
        "counts": counts,
        "accounts": rows,
        "checker_running": checker if not final else None,
        "poll_after_seconds": None if final else settings.CHECK_JOB_POLL_SECONDS,
        "url": url(job),
    }
