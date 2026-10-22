"""Several Discord channels per account, each with an optional role to mention.

Adds AccountDestination (where an account sends) and PostDelivery (one post to one channel,
with its own retries), and moves the existing data over without changing where anything goes:

- every account gets one destination: its own channel, or "the default channel" if it
  followed the default;
- every post still waiting to be sent (pending, sending, retry) gets one delivery to the
  channel it would have gone to, with its attempts, retry time and error, so it is sent
  once, not again. A waiting post with nowhere to go (no own channel and no default) is
  marked skipped: the old code would only have sent it if a channel was chosen within 24 hours;
- posts already sent or given up get no deliveries; their status stays as it was.

Going back (to 0007) is NOT lossless once accounts have more than one channel or a role:
each account keeps only its first destination, roles are dropped, and a post keeps only one
delivery's state. For a full rollback, restore the backup made before the upgrade.
The old columns are removed in 0009.
"""
import django.db.models.deletion
from django.db import migrations, models

WAITING = ("pending", "sending", "retry")


def to_destinations(apps, schema_editor):
    Account = apps.get_model("bluesky_monitor", "MonitoredAccount")
    Destination = apps.get_model("bluesky_monitor", "AccountDestination")
    SeenPost = apps.get_model("bluesky_monitor", "SeenPost")
    Delivery = apps.get_model("bluesky_monitor", "PostDelivery")
    Webhook = apps.get_model("discord_alerts", "DiscordWebhook")

    for account in Account.objects.order_by("id"):
        Destination.objects.create(account=account, channel_id=account.channel_id, role_id="")

    default = Webhook.objects.filter(is_default=True).order_by("id").first()
    for post in SeenPost.objects.filter(status__in=WAITING).select_related("account").order_by("id"):
        channel_id = post.account.channel_id or (default.pk if default else None)
        if channel_id is None:
            SeenPost.objects.filter(pk=post.pk).update(status="skipped")
            continue
        Delivery.objects.create(post=post, channel_id=channel_id, role_id="", status=post.status,
                                attempts=post.attempts, claim_token=post.claim_token, claimed_at=post.claimed_at,
                                next_attempt_at=post.next_attempt_at, last_error=post.last_error)
        SeenPost.objects.filter(pk=post.pk).update(status="pending")


def to_single_channel(apps, schema_editor):
    """Lossy (see above): the first destination, and one delivery's state per post."""
    Account = apps.get_model("bluesky_monitor", "MonitoredAccount")
    Destination = apps.get_model("bluesky_monitor", "AccountDestination")
    SeenPost = apps.get_model("bluesky_monitor", "SeenPost")
    Delivery = apps.get_model("bluesky_monitor", "PostDelivery")

    for account in Account.objects.order_by("id"):
        first = Destination.objects.filter(account=account).order_by("id").first()
        Account.objects.filter(pk=account.pk).update(channel_id=first.channel_id if first else None)

    for post in SeenPost.objects.filter(deliveries__isnull=False).distinct().order_by("id"):
        deliveries = Delivery.objects.filter(post=post)
        delivery = deliveries.filter(status__in=WAITING).order_by("id").first() or deliveries.order_by("-id").first()
        status = delivery.status if delivery.status in WAITING else post.status
        SeenPost.objects.filter(pk=post.pk).update(
            status=status, attempts=delivery.attempts, claim_token=delivery.claim_token,
            claimed_at=delivery.claimed_at, next_attempt_at=delivery.next_attempt_at, last_error=delivery.last_error)
    SeenPost.objects.filter(status="partly_sent").update(status="sent")


class Migration(migrations.Migration):

    dependencies = [
        ("discord_alerts", "0003_channel_guild_id"),
        ("bluesky_monitor", "0007_remove_legacy_webhook_url"),
    ]

    operations = [
        migrations.AlterField(
            model_name="seenpost",
            name="status",
            field=models.CharField(choices=[("seeded", "seeded"), ("pending", "pending"), ("sent", "sent"),
                                            ("partly_sent", "partly_sent"), ("failed", "failed"),
                                            ("skipped", "skipped")],
                                   default="pending", max_length=12),
        ),
        migrations.CreateModel(
            name="AccountDestination",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("role_id", models.CharField(blank=True, default="", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                              related_name="destinations", to="bluesky_monitor.monitoredaccount")),
                ("channel", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                              related_name="destinations", to="discord_alerts.discordwebhook")),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.AddConstraint(
            model_name="accountdestination",
            constraint=models.UniqueConstraint(fields=("account", "channel"), name="destination_one_per_channel"),
        ),
        migrations.AddConstraint(
            model_name="accountdestination",
            constraint=models.UniqueConstraint(condition=models.Q(("channel__isnull", True)), fields=("account",),
                                               name="destination_one_default"),
        ),
        migrations.CreateModel(
            name="PostDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("role_id", models.CharField(blank=True, default="", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("status", models.CharField(choices=[("pending", "pending"), ("sending", "sending"),
                                                     ("retry", "retry"), ("sent", "sent"), ("failed", "failed"),
                                                     ("cancelled", "cancelled")],
                                            default="pending", max_length=10)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("claim_token", models.CharField(blank=True, max_length=64)),
                ("claimed_at", models.DateTimeField(blank=True, null=True)),
                ("next_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, max_length=500)),
                ("channel", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                              related_name="deliveries", to="discord_alerts.discordwebhook")),
                ("post", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name="deliveries", to="bluesky_monitor.seenpost")),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.AddConstraint(
            model_name="postdelivery",
            constraint=models.UniqueConstraint(fields=("post", "channel"), name="delivery_once_per_channel"),
        ),
        migrations.AddIndex(
            model_name="postdelivery",
            index=models.Index(fields=["status", "next_attempt_at"], name="delivery_status_next"),
        ),
        migrations.RunPython(to_destinations, to_single_channel),
    ]
