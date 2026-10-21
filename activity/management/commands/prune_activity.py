from django.conf import settings
from django.core.management.base import BaseCommand

from activity.retention import prune


class Command(BaseCommand):
    help = ("Apply the activity log limits now (ACTIVITY_RETENTION_DAYS, ACTIVITY_MAX_EVENTS). "
            "Only log entries are removed; posts waiting to be sent are never touched.")

    def handle(self, *args, **options):
        removed = prune()
        self.stdout.write(
            f"Removed {removed['too_old']} entries older than {settings.ACTIVITY_RETENTION_DAYS} days and "
            f"{removed['over_limit']} over the {settings.ACTIVITY_MAX_EVENTS}-entry limit.")
