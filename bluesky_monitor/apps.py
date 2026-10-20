import os
import sys

from django.apps import AppConfig


def should_start_in_process(argv=None, environ=None) -> bool:
    """Start the checker inside this process only for the server that start.bat runs.

    `runserver` with auto-reload runs two processes; only the inner one (RUN_MAIN=true)
    serves requests, so only it starts the checker. Other commands (migrate, test, shell,
    gunicorn workers, ...) never start one. Set SCHEDULER_IN_WEB=0 when the checker runs
    as its own process (`manage.py run_scheduler`). Even if two do start, the database
    lease lets only one of them poll.
    """
    argv = sys.argv if argv is None else argv
    environ = os.environ if environ is None else environ
    if environ.get("SCHEDULER_IN_WEB", "1") == "0":
        return False
    if len(argv) < 2 or argv[1] != "runserver":
        return False
    return environ.get("RUN_MAIN") == "true" or "--noreload" in argv


class BlueskyMonitorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "bluesky_monitor"

    def ready(self):
        from . import db  # noqa: F401  (SQLite settings for several processes)

        if should_start_in_process():
            from .scheduler import start
            start()
