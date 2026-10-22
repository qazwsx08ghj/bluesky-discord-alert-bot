"""Step 10: remove the settings' rollback copies.

- discord_webhook_url: a copy of the default channel's URL since step 4. If it holds a URL
  and there is no default channel (data written by step 3 code after a rollback), that URL
  becomes the default channel first, so the default destination is kept.
- env_mirror_*: the state of the .env copy, which is no longer written.

Going back re-creates the columns and fills discord_webhook_url from the default channel.
"""
from django.db import migrations


def keep_the_default(apps, schema_editor):
    Settings = apps.get_model("botconfig", "BotSettings")
    Webhook = apps.get_model("discord_alerts", "DiscordWebhook")
    row = Settings.objects.filter(pk=1).first()
    url = (row.discord_webhook_url or "").strip() if row else ""
    if not url or Webhook.objects.filter(is_default=True).exists():
        return
    channel = Webhook.objects.filter(url=url).order_by("created_at", "id").first()
    if channel is None:
        name, n = "Default channel", 2
        while Webhook.objects.filter(name__iexact=name).exists():
            name, n = f"Default channel ({n})", n + 1
        channel = Webhook.objects.create(name=name, url=url)
    Webhook.objects.filter(pk=channel.pk).update(is_default=True)


def restore_copy(apps, schema_editor):
    Settings = apps.get_model("botconfig", "BotSettings")
    Webhook = apps.get_model("discord_alerts", "DiscordWebhook")
    default = Webhook.objects.filter(is_default=True).first()
    Settings.objects.filter(pk=1).update(discord_webhook_url=default.url if default else "")


class Migration(migrations.Migration):

    dependencies = [
        ("botconfig", "0001_bot_settings"),
        ("discord_alerts", "0002_channel_health_and_single_default"),
        # Added in 1.0.0: bluesky_monitor 0003 reads discord_webhook_url, which this removes.
        # The order was right before only by chance. Every install that has this migration
        # also has that one, so nothing changes for existing databases.
        ("bluesky_monitor", "0003_account_channel"),
    ]

    operations = [
        migrations.RunPython(keep_the_default, restore_copy),
        migrations.RemoveField(model_name="botsettings", name="discord_webhook_url"),
        migrations.RemoveField(model_name="botsettings", name="env_mirror_ok"),
        migrations.RemoveField(model_name="botsettings", name="env_mirror_error"),
        migrations.RemoveField(model_name="botsettings", name="env_mirror_at"),
    ]
