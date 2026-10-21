from django.apps import AppConfig


class BotconfigConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "botconfig"
    verbose_name = "Bot settings"

    def ready(self):
        from . import checks  # noqa: F401  (registers the private-files check)
        from config import security  # noqa: F401  (registers the local-only check)
