import uuid

from django.db import models
from django.utils import timezone

POST, REPOST, REPLY = "post", "repost", "reply"
KIND_CHOICES = [(POST, "Post"), (REPOST, "Repost"), (REPLY, "Reply")]


class MonitoredAccount(models.Model):
    # What is announced when an announce_* field is null (accounts added by older code).
    ANNOUNCE_DEFAULTS = {POST: True, REPOST: True, REPLY: False}

    handle = models.CharField(max_length=255, unique=True, help_text="e.g. user.bsky.social")
    display_name = models.CharField(max_length=255, blank=True)
    # Where this account's posts go: its destinations (AccountDestination, one per channel).
    is_active = models.BooleanField(default=True)
    first_poll_done = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    # Per-account check lock, shared by every process through the database.
    # A lock whose time has passed counts as free, so a crashed check never blocks forever.
    check_lock_token = models.CharField(max_length=64, blank=True)
    check_lock_until = models.DateTimeField(null=True, blank=True)

    # Result of the latest feed check (used by /api/status/). All nullable, so older code
    # can still insert accounts after a rollback.
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_check_ok = models.BooleanField(null=True, blank=True)
    last_check_category = models.CharField(max_length=32, null=True, blank=True)  # activity.problems code

    # Everything below is nullable for the same reason (rows from older versions).
    # What gets announced. Changing it only affects posts noticed afterwards.
    announce_posts = models.BooleanField(null=True, default=True)
    announce_reposts = models.BooleanField(null=True, default=True)
    announce_replies = models.BooleanField(null=True, default=False)
    # The first check, which notes existing posts without sending them.
    first_checked_at = models.DateTimeField(null=True, blank=True)
    first_check_posts = models.PositiveIntegerField(null=True, blank=True)
    # Last check that worked, and the most recent failure (kept after recovery).
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_error_category = models.CharField(max_length=32, null=True, blank=True)

    def save(self, *args, **kwargs):
        adding = self._state.adding
        super().save(*args, **kwargs)
        if adding:  # a new account follows the default channel until it is given others
            AccountDestination.objects.get_or_create(account=self, channel=None)

    def announces(self, kind: str) -> bool:
        value = {POST: self.announce_posts, REPOST: self.announce_reposts, REPLY: self.announce_replies}.get(kind)
        return self.ANNOUNCE_DEFAULTS.get(kind, True) if value is None else value

    def __str__(self):
        return self.handle


class AccountDestination(models.Model):
    """One place a watched account's notifications go, with an optional Discord role to mention.

    channel null means "the default channel": changing the default moves these, and never
    touches destinations with their own channel. An account has 1 to MAX_PER_ACCOUNT
    destinations, at most one per channel and at most one that follows the default.
    PROTECT: a channel can't be deleted while a destination uses it (see channels.delete_channel).
    """
    MAX_PER_ACCOUNT = 10

    account = models.ForeignKey(MonitoredAccount, on_delete=models.CASCADE, related_name="destinations")
    channel = models.ForeignKey("discord_alerts.DiscordWebhook", null=True, blank=True,
                                on_delete=models.PROTECT, related_name="destinations")
    role_id = models.CharField(max_length=20, blank=True, default="")  # "" = no mention
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["account", "channel"], name="destination_one_per_channel"),
            models.UniqueConstraint(fields=["account"], condition=models.Q(channel__isnull=True),
                                    name="destination_one_default"),
        ]

    def __str__(self):
        return f"{self.account_id} → {self.channel_id or 'default'}"


class SeenPost(models.Model):
    """One Bluesky post the bot has noticed for one account.

    A row is created (claimed) before anything is sent, together with one PostDelivery per
    channel it goes to. The unique (account, post_uri) constraint means only one process can
    ever claim a post, so each channel gets it at most once per account, even when checks overlap.

    `status` sums up its deliveries (see delivery.refresh_post_status), so lists and clean-up
    need only this table.
    """

    SEEDED = "seeded"     # noted during the first check; never announced
    PENDING = "pending"   # at least one delivery isn't finished yet
    SENT = "sent"         # every delivery was sent
    PARTLY_SENT = "partly_sent"  # some deliveries were sent, some gave up
    FAILED = "failed"     # no delivery was sent; at least one gave up
    SKIPPED = "skipped"   # not to be sent: no Discord channel, the kind is off, or every delivery was cancelled
    STATUS_CHOICES = [(s, s) for s in (SEEDED, PENDING, SENT, PARTLY_SENT, FAILED, SKIPPED)]

    account = models.ForeignKey(MonitoredAccount, on_delete=models.CASCADE, related_name="seen_posts")
    post_uri = models.CharField(max_length=512)
    post_cid = models.CharField(max_length=255)
    text = models.TextField(blank=True)
    post_url = models.URLField(blank=True)
    seen_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDING)
    sent_at = models.DateTimeField(null=True, blank=True)  # the newest successful delivery
    # post, repost or reply. Null for posts noted by older versions.
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["account", "post_uri"], name="seenpost_unique_per_account"),
        ]
        indexes = [models.Index(fields=["account", "status"], name="seenpost_account_status")]

    def __str__(self):
        return f"{self.account.handle} — {self.post_uri}"


class PostDelivery(models.Model):
    """One post's notification to one Discord channel, and where it stands.

    Created together with its post, for each channel the account sent to at that moment,
    so changing an account's channels or roles later never sends old posts. The unique
    (post, channel) constraint means a channel gets a post at most once. Each delivery has
    its own attempts and retries, so a failing channel never holds up or repeats the others.
    """

    PENDING = "pending"     # waiting to be sent
    SENDING = "sending"     # one process is sending it right now
    RETRY = "retry"         # a send failed; try again at next_attempt_at
    SENT = "sent"           # Discord accepted it
    FAILED = "failed"       # gave up after the last retry or the retry window
    CANCELLED = "cancelled"  # the channel was removed from the account (or deleted) before it was sent
    STATUS_CHOICES = [(s, s) for s in (PENDING, SENDING, RETRY, SENT, FAILED, CANCELLED)]
    UNFINISHED = (PENDING, SENDING, RETRY)
    CANCELLABLE = (PENDING, RETRY)  # a send in progress can't be recalled

    post = models.ForeignKey(SeenPost, on_delete=models.CASCADE, related_name="deliveries")
    channel = models.ForeignKey("discord_alerts.DiscordWebhook", on_delete=models.CASCADE,
                                related_name="deliveries")
    role_id = models.CharField(max_length=20, blank=True, default="")  # as set when the post was noticed
    created_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    claim_token = models.CharField(max_length=64, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["post", "channel"], name="delivery_once_per_channel"),
        ]
        indexes = [models.Index(fields=["status", "next_attempt_at"], name="delivery_status_next")]

    def __str__(self):
        return f"post {self.post_id} → channel {self.channel_id}: {self.status}"


class SchedulerState(models.Model):
    """Single row (pk=1) shared by every process: who runs the checker, and its heartbeat.

    The lease works across processes because it is taken with one conditional UPDATE.
    A lease that is not renewed before lease_expires_at is stale and can be taken over.
    """

    instance_id = models.CharField(max_length=128, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    cycle_started_at = models.DateTimeField(null=True, blank=True)
    cycle_finished_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    last_cycle_error = models.CharField(max_length=500, blank=True)  # technical, secrets removed
    # What the last finished check did: {"result", "category", "accounts_checked",
    # "accounts_failed", "posts_sent"}. See poller.CycleSummary. Null until the first check.
    last_cycle_summary = models.JSONField(null=True, blank=True)


class BlueskySessionState(models.Model):
    """Single row (pk=1): the bot's shared Bluesky login session and login back-off.

    credential_fingerprint is a hash of handle + password, so a saved session is only
    reused for the same credentials, and changing them skips any back-off.
    """

    handle = models.CharField(max_length=255, blank=True)
    credential_fingerprint = models.CharField(max_length=64, blank=True)
    session_string = models.TextField(blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)
    login_retry_after = models.DateTimeField(null=True, blank=True)
    login_failures = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)


class CheckJob(models.Model):
    """One "Check all" request, run in the background by the checker (see jobs.py).

    The web request only creates the row (status queued) and answers 202. The checker
    that holds the scheduler lease picks it up, checks the accounts one by one (with the
    same per-account locks and post claims as the scheduled check) and writes progress
    and per-account results here, so they survive a browser refresh.
    """

    QUEUED = "queued"
    RUNNING = "running"
    FINISHED = "finished"
    INTERRUPTED = "interrupted"  # the checker stopped while running it
    EXPIRED = "expired"  # no checker picked it up in time
    STATUS_CHOICES = [(s, s) for s in (QUEUED, RUNNING, FINISHED, INTERRUPTED, EXPIRED)]
    ACTIVE = (QUEUED, RUNNING)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=20, default="check_all")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=QUEUED, db_index=True)
    requested_at = models.DateTimeField(default=timezone.now, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)
    worker = models.CharField(max_length=128, blank=True)  # the checker instance running it
    total = models.PositiveIntegerField(default=0)  # active accounts to check
    done = models.PositiveIntegerField(default=0)
    current_account_id = models.BigIntegerField(null=True, blank=True)
    current_handle = models.CharField(max_length=255, blank=True)
    result = models.CharField(max_length=20, blank=True)  # check_all._overall: ok, partial, failed, ...
    message = models.TextField(blank=True)
    problem_category = models.CharField(max_length=50, blank=True)
    counts = models.JSONField(null=True, blank=True)
    accounts = models.JSONField(default=list, blank=True)  # one row per account, see check_all._account_row

    class Meta:
        ordering = ["-requested_at"]
