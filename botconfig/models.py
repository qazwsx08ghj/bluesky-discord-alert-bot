from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

MIN_INTERVAL, MAX_INTERVAL = 1, 60


class BotSettings(models.Model):
    """The bot's settings. One row (pk=1). The source of truth since step 3.

    Filled once from the environment (.env) the first time it's needed; never
    overwritten from .env after that. See botconfig/store.py.
    """
    SOURCE_ENV = "env_import"
    SOURCE_DEFAULTS = "defaults"

    bluesky_handle = models.CharField(max_length=255, blank=True)
    bluesky_password = models.CharField(max_length=255, blank=True)  # write-only; never returned or logged
    # The default Discord channel. Moves to a DiscordWebhook row in step 4.
    discord_webhook_url = models.CharField(max_length=500, blank=True)
    poll_interval_minutes = models.PositiveSmallIntegerField(
        default=5, validators=[MinValueValidator(MIN_INTERVAL), MaxValueValidator(MAX_INTERVAL)])

    version = models.PositiveIntegerField(default=1)  # +1 on every save; stamped into .env
    source = models.CharField(max_length=20, default=SOURCE_DEFAULTS)
    imported_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # State of the .env copy kept for rollback (see docs/SETTINGS_MIGRATION.md)
    env_mirror_ok = models.BooleanField(default=True)
    env_mirror_error = models.CharField(max_length=300, blank=True)
    env_mirror_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "bot settings"

    def __str__(self):  # never include the password
        return f"BotSettings v{self.version}"

    __repr__ = __str__
