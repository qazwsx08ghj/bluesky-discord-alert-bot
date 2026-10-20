"""The background checker.

Only one checker may poll at a time, across all processes. Each checker has a unique
instance id and must hold the database lease (locks.acquire_scheduler_lease) to run a
cycle. A heartbeat renews the lease every SCHEDULER_HEARTBEAT_SECONDS; a checker that
dies stops renewing, its lease expires, and a standby checker takes over.

Jobs live in memory (no job store in the database), so nothing stale is reloaded on start.
"""
import atexit
import logging
import os
import signal
import socket
import uuid

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from . import locks
from .models import SchedulerState

logger = logging.getLogger(__name__)

INSTANCE_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
POLL_JOB_ID = "poll_bluesky"

_scheduler = None


def heartbeat(instance_id: str = INSTANCE_ID) -> bool:
    """Take or renew the lease. Returns True while this instance is the active checker."""
    close_old_connections()
    held = locks.acquire_scheduler_lease(instance_id)
    if held and _scheduler is not None:
        job = _scheduler.get_job(POLL_JOB_ID)
        if job is not None and job.next_run_time:
            SchedulerState.objects.filter(pk=1, instance_id=instance_id).update(next_run_at=job.next_run_time)
    return held


def run_cycle(instance_id: str = INSTANCE_ID) -> bool:
    """One check of every active account. Returns False if skipped (no lease)."""
    from .poller import poll_all_active_accounts

    close_old_connections()
    if not locks.acquire_scheduler_lease(instance_id):
        logger.info("Another checker is active; this one is on standby")
        return False

    SchedulerState.objects.filter(pk=1).update(cycle_started_at=timezone.now(), last_cycle_error="")
    error = ""
    try:
        poll_all_active_accounts(should_continue=lambda: locks.holds_scheduler_lease(instance_id))
    except Exception as exc:  # keep the checker alive; record the problem
        logger.exception("Check cycle failed")
        error = str(exc)[:500]
    SchedulerState.objects.filter(pk=1, instance_id=instance_id).update(
        cycle_finished_at=timezone.now(), last_cycle_error=error
    )
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
        trigger=IntervalTrigger(minutes=settings.POLL_INTERVAL_MINUTES),
        id=POLL_JOB_ID, name="Poll all active Bluesky accounts",
        max_instances=1, coalesce=True, misfire_grace_time=60,
    )
    return scheduler


def _mark_started(instance_id: str):
    held = locks.acquire_scheduler_lease(instance_id)
    if held:
        SchedulerState.objects.filter(pk=1).update(started_at=timezone.now())
    logger.info(
        "Bluesky poller started (%s) - interval: %d minute(s)%s",
        instance_id, settings.POLL_INTERVAL_MINUTES, "" if held else " - standby, another checker is active",
    )


def start():
    """Start the checker in the background of this process (used by `runserver`)."""
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = _build(BackgroundScheduler, INSTANCE_ID)
    _scheduler.start()
    _mark_started(INSTANCE_ID)
    atexit.register(stop)


def stop():
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
    finally:
        _scheduler = None
        locks.release_scheduler_lease(INSTANCE_ID)
        logger.info("Bluesky poller stopped; lease released")


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
    try:
        _scheduler.start()
    finally:
        _scheduler = None
        locks.release_scheduler_lease(INSTANCE_ID)
        logger.info("Bluesky poller stopped; lease released")
