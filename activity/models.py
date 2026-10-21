from django.db import models
from django.utils import timezone


class ActivityEvent(models.Model):
    """One line in the activity log: something the bot did, or a problem it ran into.

    The log is separate from the delivery queue (bluesky_monitor.SeenPost): nothing here
    points at a queued post, and cleaning up the log never touches the queue.

    Accounts and channels are referenced by id plus a name snapshot, without a foreign key,
    so the log keeps its history when an account or channel is deleted, and deleting one is
    never blocked by its log entries (also with older code after a rollback).

    Repeats of the same problem for the same account/channel within an hour are counted on
    one event (repeat_count, last_seen_at) instead of filling the log.
    """

    SUCCESS, INFO, WARNING, ERROR = "success", "info", "warning", "error"
    LEVELS = [(x, x) for x in (SUCCESS, INFO, WARNING, ERROR)]

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    repeat_count = models.PositiveIntegerField(default=1)

    type = models.CharField(max_length=32)
    level = models.CharField(max_length=10, choices=LEVELS)
    category = models.CharField(max_length=32, blank=True)  # activity.problems code, for problems
    message = models.CharField(max_length=500)              # plain language, shown to the user

    account_id = models.BigIntegerField(null=True, blank=True)
    account_label = models.CharField(max_length=255, blank=True)
    channel_id = models.BigIntegerField(null=True, blank=True)
    channel_label = models.CharField(max_length=100, blank=True)

    attempt = models.PositiveSmallIntegerField(null=True, blank=True)  # send attempt number; >1 = a retry
    detail = models.JSONField(default=dict, blank=True)               # post link, next retry time, counts
    technical = models.CharField(max_length=1000, blank=True)         # for debugging; secrets removed

    class Meta:
        indexes = [
            models.Index(fields=["type", "id"], name="activity_type_id"),
            models.Index(fields=["level", "id"], name="activity_level_id"),
            models.Index(fields=["account_id", "id"], name="activity_account_id"),
            models.Index(fields=["channel_id", "id"], name="activity_channel_id"),
        ]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.type}: {self.message}"
