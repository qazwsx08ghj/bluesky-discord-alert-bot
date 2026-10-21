"""Wait until the database exists and every migration is applied (Docker checker service).

The web service applies migrations (with a backup first). After Docker Desktop restarts,
both services start at once, so the checker waits here instead of using a half-updated
database. Exits 1 after --timeout seconds; Docker then restarts it and it waits again.
"""
import time

from django.core.management.base import BaseCommand
from django.db import connections

from botconfig.management.commands.backup_db import pending_migrations


class Command(BaseCommand):
    help = "Wait until the database is ready and fully migrated."

    def add_arguments(self, parser):
        parser.add_argument("--timeout", type=float, default=300)
        parser.add_argument("--interval", type=float, default=2)

    def handle(self, *args, timeout=300, interval=2, **options):
        deadline = time.monotonic() + timeout
        said = False
        while True:
            try:
                if not pending_migrations():
                    self.stdout.write("The database is ready.")
                    return
                reason = "waiting for the database update to finish"
            except Exception as exc:  # no database file yet, or it is locked for the update
                reason = f"waiting for the database ({type(exc).__name__})"
            finally:
                connections.close_all()
            if not said:
                self.stdout.write(f"Checker: {reason}...")
                said = True
            if time.monotonic() > deadline:
                self.stderr.write("The database wasn't ready in time; Docker will start the checker again.")
                raise SystemExit(1)
            time.sleep(interval)
