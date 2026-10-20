from django.db import models


class MonitoredAccount(models.Model):
    handle = models.CharField(max_length=255, unique=True, help_text="e.g. user.bsky.social")
    display_name = models.CharField(max_length=255, blank=True)
    discord_webhook_url = models.URLField(
        blank=True,
        help_text="Override the default webhook for this account",
    )
    is_active = models.BooleanField(default=True)
    first_poll_done = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.handle


class SeenPost(models.Model):
    account = models.ForeignKey(MonitoredAccount, on_delete=models.CASCADE, related_name="seen_posts")
    post_uri = models.CharField(max_length=512, unique=True)
    post_cid = models.CharField(max_length=255)
    text = models.TextField(blank=True)
    post_url = models.URLField(blank=True)
    seen_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.account.handle} — {self.post_uri}"
