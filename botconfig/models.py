from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

MIN_INTERVAL, MAX_INTERVAL = 1, 60


class BotSettings(models.Model):
    """The bot's settings. One row (pk=1), the only place they are kept.

    Filled once from an earlier install's .env the first time it's needed; never
    overwritten from .env after that. See botconfig/store.py. The default Discord
    channel is a DiscordWebhook row (discord_alerts), not a field here.
    """
    SOURCE_ENV = "env_import"
    SOURCE_DEFAULTS = "defaults"

    bluesky_handle = models.CharField(max_length=255, blank=True)
    bluesky_password = models.CharField(max_length=255, blank=True)  # write-only; never returned or logged
    poll_interval_minutes = models.PositiveSmallIntegerField(
        default=5, validators=[MinValueValidator(MIN_INTERVAL), MaxValueValidator(MAX_INTERVAL)])

    version = models.PositiveIntegerField(default=1)  # +1 on every save
    source = models.CharField(max_length=20, default=SOURCE_DEFAULTS)
    imported_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "bot settings"

    def __str__(self):  # never include the password
        return f"BotSettings v{self.version}"

    __repr__ = __str__
