"""Step 10: remove the account's old webhook URL column (a rollback copy since step 4).

Routing has used the account's channel since step 4, and the column only mirrored it.
Before the column goes, any URL that somehow has no channel (for example data written by
step 3 code after a rollback) gets one, so no account loses its destination.
Going back re-creates the column and fills it from the channels again.
"""
from django.db import migrations


def _unique_name(Webhook, base):
    name, n = base[:255], 2
    while Webhook.objects.filter(name__iexact=name).exists():
        suffix = f" ({n})"
        name, n = base[:255 - len(suffix)] + suffix, n + 1
    return name


def keep_every_route(apps, schema_editor):
    Webhook = apps.get_model("discord_alerts", "DiscordWebhook")
    Account = apps.get_model("bluesky_monitor", "MonitoredAccount")
    for account in Account.objects.filter(channel=None).exclude(discord_webhook_url="").order_by("id"):
        url = account.discord_webhook_url
        channel = Webhook.objects.filter(url=url).order_by("created_at", "id").first()
        if channel is None:
            channel = Webhook.objects.create(name=_unique_name(Webhook, f"Channel for @{account.handle}"), url=url)
        Account.objects.filter(pk=account.pk).update(channel=channel)


def restore_copies(apps, schema_editor):
    Account = apps.get_model("bluesky_monitor", "MonitoredAccount")
    for account in Account.objects.exclude(channel=None).select_related("channel"):
        Account.objects.filter(pk=account.pk).update(discord_webhook_url=account.channel.url)


class Migration(migrations.Migration):

    dependencies = [
        ("bluesky_monitor", "0006_check_jobs"),
        ("discord_alerts", "0002_channel_health_and_single_default"),
    ]

    operations = [
        migrations.RunPython(keep_every_route, restore_copies),
        migrations.RemoveField(model_name="monitoredaccount", name="discord_webhook_url"),
    ]
