"""The Discord server a channel's webhook posts to, learned before the first role mention.

Runs after botconfig 0002 and bluesky_monitor 0007, whose ways back read the channels:
they then see them as they were, without this column.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("discord_alerts", "0002_channel_health_and_single_default"),
        ("botconfig", "0002_remove_legacy_copies"),
        ("bluesky_monitor", "0007_remove_legacy_webhook_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="discordwebhook",
            name="guild_id",
            field=models.CharField(blank=True, default="", max_length=20, null=True),
        ),
    ]
