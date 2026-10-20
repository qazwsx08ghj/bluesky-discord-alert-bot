from rest_framework import serializers
from .models import DiscordWebhook


class DiscordWebhookSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiscordWebhook
        fields = ["id", "name", "url", "is_default", "created_at"]
