"""The background checker.

Only one checker may poll at a time, across all processes. Each checker has a unique
instance id and must hold the database lease (locks.acquire_scheduler_lease) to run a
cycle. A heartbeat renews the lease every SCHEDULER_HEARTBEAT_SECONDS; a checker that
dies stops renewing, its lease expires, and a standby checker takes over.

Jobs live in memory (no job store in the database), so nothing stale is reloaded on start.

Health for /api/status/ (activity/status.py) comes from SchedulerState: the heartbeat,
when the latest check started and finished, what it did (last_cycle_summary), and when
the next one is due.

"Check all" requests from the panel are queued as CheckJob rows. A small thread
(JobWorker) in the checker runs them in the background; see jobs.py.
"""
import atexit
import logging
import os
import signal
import socket
import threading
import time
import traceback
import uuid
from datetime import timedelta

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from django.conf import settings
from django.db import close_old_connections, connection
from django.utils import timezone

from activity import log as activity, retention
from . import retention as post_retention
from activity.models import ActivityEvent
from botconfig import compat
from botconfig.secrets import scrub
from . import locks
from .models import SchedulerState

logger = logging.getLogger(__name__)

INSTANCE_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
POLL_JOB_ID = "poll_bluesky"

_scheduler = None
_worker = None
_last_heartbeat = time.monotonic()  # when heartbeat() last finished (for the watchdog)
_active = set()  # instance ids in this process that currently hold the lease


class JobWorker(threading.Thread):
    """Runs queued "Check all" jobs (jobs.run_pending) every CHECK_JOB_POLL_SECONDS.

    A thread of its own, so a long Check All never delays the scheduled check or the
    heartbeat. Only the checker holding the lease actually runs jobs.
    """

    def __init__(self, instance_id: str):
        super().__init__(name="check-all-jobs", daemon=True)
        self.instance_id = instance_id
        self.stopping = threading.Event()

    def run(self):
        from . import jobs

        try:
            while not self.stopping.wait(settings.CHECK_JOB_POLL_SECONDS):
                try:
                    jobs.run_pending(self.instance_id)
                except Exception:  # keep looking for jobs; the problem is in the log
                    logger.exception("Running Check all jobs failed")
        finally:
            connection.close()

    def stop(self):
        self.stopping.set()


def _start_worker(instance_id: str):
    global _worker
    if _worker is None:
        _worker = JobWorker(instance_id)
        _worker.start()


def _stop_worker():
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None


def _note_lease(instance_id: str, held: bool):
    """Log when this checker becomes the active one (at start, or taking over from a stopped one)."""
    if held and instance_id not in _active:
        _active.add(instance_id)
        activity.record(activity.CHECKER_STARTED, ActivityEvent.INFO,
                        f"The checker started. It checks every {_interval_minutes()} minute(s).",
                        detail={"instance": instance_id})
        retention.prune_quietly()
    elif not held:
        _active.discard(instance_id)


def _interval_minutes() -> int:
    from botconfig import store

    return store.poll_interval_minutes()


def apply_interval(scheduler=None) -> bool:
    """Reschedule the check job if the saved interval changed. Returns True if it did."""
    scheduler = scheduler or _scheduler
    if scheduler is None:
        return False
    job = scheduler.get_job(POLL_JOB_ID)
    minutes = _interval_minutes()
    if job is None or job.trigger.interval == timedelta(minutes=minutes):
        return False
    scheduler.reschedule_job(POLL_JOB_ID, trigger=IntervalTrigger(minutes=minutes))
    logger.info("Check interval changed to %d minute(s)", minutes)
    return True


def heartbeat(instance_id: str = INSTANCE_ID) -> bool:
    """Take or renew the lease, and pick up a changed check interval.

    Returns True while this instance is the active checker.
    """
    close_old_connections()
    held = locks.acquire_scheduler_lease(instance_id)
    _note_lease(instance_id, held)
    try:
        apply_interval()
    except Exception:  # never let a settings problem stop the heartbeat
        logger.exception("Couldn't apply the check interval")
    if held and _scheduler is not None:
        job = _scheduler.get_job(POLL_JOB_ID)
        if job is not None and job.next_run_time:
            SchedulerState.objects.filter(pk=1, instance_id=instance_id).update(next_run_at=job.next_run_time)
    global _last_heartbeat
    _last_heartbeat = time.monotonic()
    return held


class Watchdog(threading.Thread):
    """Stops a standalone checker whose heartbeat has stalled (a hang, or a database that
    stopped answering), so Docker's restart policy starts a fresh one. Only used by
    run_forever(); the checker inside `runserver` is never stopped this way."""

    def __init__(self, limit_seconds: float, exit=os._exit, check_every: float = 10):
        super().__init__(name="checker-watchdog", daemon=True)
        self.limit, self.exit, self.check_every = limit_seconds, exit, check_every
        self.stopping = threading.Event()

    def stalled(self, now=None) -> bool:
        return (now if now is not None else time.monotonic()) - _last_heartbeat > self.limit

    def run(self):
        while not self.stopping.wait(self.check_every):
            if self.stalled():
                logger.critical("The checker's heartbeat hasn't run for %d seconds; stopping so it is restarted",
                                self.limit)
                self.exit(70)
                return

    def stop(self):
        self.stopping.set()


def run_cycle(instance_id: str = INSTANCE_ID) -> bool:
    """One check of every active account. Returns False if skipped (no lease)."""
    from .poller import poll_all_active_accounts

    close_old_connections()
    newer = compat.unknown_migrations()
    if newer:  # a newer version updated the data: this one must not check, send or write anything
        logger.error("Not checking: %s", compat.explanation(newer))
        return False
    if not locks.acquire_scheduler_lease(instance_id):
        logger.info("Another checker is active; this one is on standby")
        return False

    from .poller import CycleSummary

    SchedulerState.objects.filter(pk=1).update(cycle_started_at=timezone.now(), last_cycle_error="")
    error = ""
    try:
        summary = poll_all_active_accounts(should_continue=lambda: locks.holds_scheduler_lease(instance_id))
    except Exception as exc:  # keep the checker alive; record the problem
        logger.exception("Check cycle failed")
        error = scrub(str(exc))[:500]
        summary = CycleSummary(result="failed", category="internal")
        activity.problem(activity.CYCLE_FAILED, "internal",
                         "A check stopped because of an unexpected error. The bot tries again at the next check.",
                         technical="".join(traceback.format_exception(exc))[-1000:], merge_repeats=True)
    SchedulerState.objects.filter(pk=1, instance_id=instance_id).update(
        cycle_finished_at=timezone.now(), last_cycle_error=error,
        last_cycle_summary=summary.as_dict() if isinstance(summary, CycleSummary) else None,
    )
    retention.prune_quietly()
    post_retention.prune_quietly()
    return True


def _build(scheduler_cls, instance_id: str):
    scheduler = scheduler_cls(timezone="UTC", jobstores={"default": MemoryJobStore()})
    scheduler.add_job(
        heartbeat, kwargs={"instance_id": instance_id},
        trigger=IntervalTrigger(seconds=settings.SCHEDULER_HEARTBEAT_SECONDS),
        id="heartbeat", next_run_time=timezone.now(), max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        run_cycle, kwargs={"instance_id": instance_id},
        trigger=IntervalTrigger(minutes=_interval_minutes()),
        id=POLL_JOB_ID, name="Poll all active Bluesky accounts",
        max_instances=1, coalesce=True, misfire_grace_time=60,
    )
    return scheduler


def _mark_started(instance_id: str):
    from botconfig import store

    try:
        store.startup_check()
    except Exception:  # the checker still starts; the problem is in the log
        logger.exception("Settings check at start failed")
    held = locks.acquire_scheduler_lease(instance_id)
    if held:
        SchedulerState.objects.filter(pk=1).update(started_at=timezone.now())
    _note_lease(instance_id, held)
    logger.info(
        "Bluesky poller started (%s) - interval: %d minute(s)%s",
        instance_id, _interval_minutes(), "" if held else " - standby, another checker is active",
    )


def start():
    """Start the checker in the background of this process (used by `runserver`)."""
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = _build(BackgroundScheduler, INSTANCE_ID)
    _scheduler.start()
    _mark_started(INSTANCE_ID)
    _start_worker(INSTANCE_ID)
    atexit.register(stop)


def _release(instance_id: str):
    if instance_id in _active:
        _active.discard(instance_id)
        try:
            activity.record(activity.CHECKER_STOPPED, ActivityEvent.INFO, "The checker stopped.",
                            detail={"instance": instance_id})
        except Exception:  # shutting down; the log entry is optional
            pass
    locks.release_scheduler_lease(instance_id)
    logger.info("Bluesky poller stopped; lease released")


def stop():
    global _scheduler
    if _scheduler is None:
        return
    _stop_worker()
    try:
        _scheduler.shutdown(wait=False)
    finally:
        _scheduler = None
        _release(INSTANCE_ID)


def run_forever():
    """Run the checker in the foreground until stopped (used by `manage.py run_scheduler`)."""
    global _scheduler
    _scheduler = _build(BlockingScheduler, INSTANCE_ID)

    def _shutdown(signum, _frame):
        logger.info("Received signal %s; shutting down the checker", signum)
        _scheduler.shutdown(wait=False)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _shutdown)
    _mark_started(INSTANCE_ID)
    _start_worker(INSTANCE_ID)
    watchdog = None
    if settings.SCHEDULER_WATCHDOG_SECONDS > 0:
        global _last_heartbeat
        _last_heartbeat = time.monotonic()
        watchdog = Watchdog(settings.SCHEDULER_WATCHDOG_SECONDS)
        watchdog.start()
    try:
        _scheduler.start()
    finally:
        if watchdog is not None:
            watchdog.stop()
        _stop_worker()
        _scheduler = None
        _release(INSTANCE_ID)
