"""Docker healthcheck for the checker service: is its heartbeat recent?

Exit 0 when a checker renewed the lease within --max-age seconds, else 1. The heartbeat
runs every SCHEDULER_HEARTBEAT_SECONDS (30 s). The checker also stops itself if its own
heartbeat stalls (SCHEDULER_WATCHDOG_SECONDS), so Docker restarts it.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from bluesky_monitor.models import SchedulerState


class Command(BaseCommand):
    help = "Exit 0 if the checker's heartbeat is recent."

    def add_arguments(self, parser):
        parser.add_argument("--max-age", type=int, default=120)

    def handle(self, *args, max_age=120, **options):
        state = SchedulerState.objects.filter(pk=1).first()
        if state and state.instance_id and state.heartbeat_at and \
                timezone.now() - state.heartbeat_at < timedelta(seconds=max_age):
            self.stdout.write("ok")
            return
        self.stderr.write("The checker's heartbeat is missing or too old.")
        raise SystemExit(1)
