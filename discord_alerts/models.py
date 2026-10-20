from django.db import models


class DiscordWebhook(models.Model):
    name = models.CharField(max_length=255, unique=True, help_text="Friendly label, e.g. #announcements")
    url = models.URLField()
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.is_default:
            DiscordWebhook.objects.exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)
