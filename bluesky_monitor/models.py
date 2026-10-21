from django.db import models

POST, REPOST, REPLY = "post", "repost", "reply"
KIND_CHOICES = [(POST, "Post"), (REPOST, "Repost"), (REPLY, "Reply")]


class MonitoredAccount(models.Model):
    # What is announced when an announce_* field is null (accounts added by older code).
    ANNOUNCE_DEFAULTS = {POST: True, REPOST: True, REPLY: False}

    handle = models.CharField(max_length=255, unique=True, help_text="e.g. user.bsky.social")
    display_name = models.CharField(max_length=255, blank=True)
    # Where this account's posts go. Null means "the default channel", so changing the
    # default moves these accounts and never touches accounts with their own channel.
    # PROTECT: a channel can't be deleted while accounts still use it.
    channel = models.ForeignKey("discord_alerts.DiscordWebhook", null=True, blank=True,
                                on_delete=models.PROTECT, related_name="accounts")
    # Before step 4: the account's own webhook URL. Kept as a copy of channel.url (or "")
    # so older code still routes the same after a rollback. Not read by this version.
    discord_webhook_url = models.URLField(
        max_length=500,
        blank=True,
        help_text="Copy of the channel URL, for rollback only",
    )
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

    # Step 6. Everything below is nullable for the same reason.
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

    def announces(self, kind: str) -> bool:
        value = {POST: self.announce_posts, REPOST: self.announce_reposts, REPLY: self.announce_replies}.get(kind)
        return self.ANNOUNCE_DEFAULTS.get(kind, True) if value is None else value

    def __str__(self):
        return self.handle


class SeenPost(models.Model):
    """One Bluesky post the bot has noticed for one account, and where its Discord delivery stands.

    A row is created (claimed) before anything is sent. The unique (account, post_uri)
    constraint means only one process can ever claim a post, so it is announced at most
    once per account, even when checks overlap.
    """

    SEEDED = "seeded"     # noted during the first check; never announced
    PENDING = "pending"   # claimed, waiting to be sent
    SENDING = "sending"   # one process is sending it right now
    RETRY = "retry"       # a send failed; try again at next_attempt_at
    SENT = "sent"         # Discord accepted it
    FAILED = "failed"     # gave up after the last retry or the retry window
    SKIPPED = "skipped"   # not to be sent: no Discord channel, or the account doesn't announce this kind
    STATUS_CHOICES = [(s, s) for s in (SEEDED, PENDING, SENDING, RETRY, SENT, FAILED, SKIPPED)]

    account = models.ForeignKey(MonitoredAccount, on_delete=models.CASCADE, related_name="seen_posts")
    post_uri = models.CharField(max_length=512)
    post_cid = models.CharField(max_length=255)
    text = models.TextField(blank=True)
    post_url = models.URLField(blank=True)
    seen_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    claim_token = models.CharField(max_length=64, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    # post, repost or reply. Null for posts noted before step 6.
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["account", "post_uri"], name="seenpost_unique_per_account"),
        ]
        indexes = [models.Index(fields=["account", "status"], name="seenpost_account_status")]

    def __str__(self):
        return f"{self.account.handle} — {self.post_uri}"


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
