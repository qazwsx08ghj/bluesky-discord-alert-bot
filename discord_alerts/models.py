from django.db import models, transaction
from django.db.models import Q


class DiscordWebhook(models.Model):
    """A named Discord channel (one webhook). The only place notifications go.

    One channel may be the default. Watched accounts either name a channel explicitly or
    follow the default (MonitoredAccount.channel is null). The URL is a secret: it is never
    returned by the API or written to logs.
    """
    name = models.CharField(max_length=255, unique=True, help_text="Friendly label, e.g. #announcements")
    url = models.URLField(max_length=500)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    # Delivery health: the result of the last message sent to this channel.
    last_delivery_at = models.DateTimeField(null=True, blank=True)
    last_delivery_ok = models.BooleanField(null=True)
    # null=True so older code (which doesn't know this column) can still add channels after a rollback.
    last_delivery_error = models.CharField(max_length=300, blank=True, null=True, default="")

    class Meta:
        constraints = [
            # At most one default channel, enforced by the database.
            models.UniqueConstraint(fields=["is_default"], condition=Q(is_default=True),
                                    name="discordwebhook_single_default"),
        ]

    def __str__(self):  # never the URL
        return self.name

    def __repr__(self):
        return f"<DiscordWebhook {self.pk}: {self.name}>"

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.is_default:
                DiscordWebhook.objects.exclude(pk=self.pk).filter(is_default=True).update(is_default=False)
            super().save(*args, **kwargs)
