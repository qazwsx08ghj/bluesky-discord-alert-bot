from rest_framework import serializers

from .models import MAX_INTERVAL, MIN_INTERVAL
from .secrets import is_masked


class SettingsUpdateSerializer(serializers.Serializer):
    """Validates a settings change. Every field is optional; omitted fields stay as they are.

    - A blank password keeps the saved one (as before).
    - A masked webhook URL (as shown by GET) means "unchanged".
    """
    bluesky_handle = serializers.CharField(max_length=255, required=False, allow_blank=True, trim_whitespace=True)
    bluesky_password = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                             trim_whitespace=True, write_only=True)
    discord_webhook_url = serializers.CharField(max_length=500, required=False, allow_blank=True,
                                                trim_whitespace=True)
    poll_interval_minutes = serializers.IntegerField(required=False, min_value=MIN_INTERVAL, max_value=MAX_INTERVAL)

    def validate_bluesky_handle(self, value):
        value = value.lstrip("@")
        if any(c.isspace() for c in value):
            raise serializers.ValidationError("A Bluesky handle can't contain spaces.")
        return value

    def validate_discord_webhook_url(self, value):
        if not value or is_masked(value):
            return value
        if not value.startswith(("https://", "http://")):
            raise serializers.ValidationError("Paste the full webhook URL, starting with https://")
        return value

    def changes(self) -> dict:
        data = dict(self.validated_data)
        if is_masked(data.get("discord_webhook_url", "")):
            data.pop("discord_webhook_url")
        if not data.get("bluesky_password"):
            data.pop("bluesky_password", None)
        return data
