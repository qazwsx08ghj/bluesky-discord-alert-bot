import sys
from django.apps import AppConfig

_SKIP_CMDS = {"migrate", "makemigrations", "test", "shell", "collectstatic", "createsuperuser"}


class BlueskyMonitorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "bluesky_monitor"

    def ready(self):
        if any(cmd in sys.argv for cmd in _SKIP_CMDS):
            return
        from .scheduler import start
        start()
