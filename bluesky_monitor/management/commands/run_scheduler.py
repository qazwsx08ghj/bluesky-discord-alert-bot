from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run the Bluesky checker in the foreground (one active checker at a time, enforced by a database lease)."

    def handle(self, *args, **options):
        from bluesky_monitor.scheduler import run_forever

        self.stdout.write("Starting the Bluesky checker. Press Ctrl+C to stop.")
        run_forever()
