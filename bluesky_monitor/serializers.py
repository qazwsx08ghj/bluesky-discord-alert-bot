from rest_framework import serializers
from .models import MonitoredAccount, SeenPost


class SeenPostSerializer(serializers.ModelSerializer):
    class Meta:
        model = SeenPost
        fields = ["id", "post_uri", "post_url", "text", "seen_at"]


class MonitoredAccountSerializer(serializers.ModelSerializer):
    seen_posts = SeenPostSerializer(many=True, read_only=True)

    class Meta:
        model = MonitoredAccount
        fields = [
            "id",
            "handle",
            "display_name",
            "discord_webhook_url",
            "is_active",
            "first_poll_done",
            "created_at",
            "seen_posts",
        ]
        read_only_fields = ["first_poll_done", "created_at"]
