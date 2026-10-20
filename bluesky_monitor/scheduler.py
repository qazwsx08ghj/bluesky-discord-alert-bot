import logging
from django.conf import settings
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from django_apscheduler.jobstores import DjangoJobStore

logger = logging.getLogger(__name__)

_scheduler = None


def start():
    global _scheduler
    if _scheduler is not None:
        return

    from .poller import poll_all_active_accounts

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_jobstore(DjangoJobStore(), "default")

    _scheduler.add_job(
        poll_all_active_accounts,
        trigger=IntervalTrigger(minutes=settings.POLL_INTERVAL_MINUTES),
        id="poll_bluesky",
        name="Poll all active Bluesky accounts",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info(
        "Bluesky poller started — interval: %d minute(s)", settings.POLL_INTERVAL_MINUTES
    )
