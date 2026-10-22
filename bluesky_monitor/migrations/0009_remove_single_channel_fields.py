"""Remove the single-channel columns that 0008 moved to AccountDestination and PostDelivery.

Going back re-creates them (empty), and 0008's reverse fills them again from the first
destination and one delivery per post. See 0008 for why that isn't lossless.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("bluesky_monitor", "0008_destinations_and_deliveries"),
    ]

    operations = [
        migrations.RemoveField(model_name="monitoredaccount", name="channel"),
        migrations.RemoveField(model_name="seenpost", name="attempts"),
        migrations.RemoveField(model_name="seenpost", name="claim_token"),
        migrations.RemoveField(model_name="seenpost", name="claimed_at"),
        migrations.RemoveField(model_name="seenpost", name="next_attempt_at"),
        migrations.RemoveField(model_name="seenpost", name="last_error"),
    ]
