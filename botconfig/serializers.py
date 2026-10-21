from rest_framework import serializers

from .models import MAX_INTERVAL, MIN_INTERVAL


class SettingsUpdateSerializer(serializers.Serializer):
    """Validates a settings change. Every field is optional; omitted fields stay as they are.

    A blank password keeps the saved one. Discord channels (also the default one) are
    managed under /api/webhooks/; the old `discord_webhook_url` field was removed in step 10
    and is refused with a pointer there, instead of being silently ignored.
    """
    bluesky_handle = serializers.CharField(max_length=255, required=False, allow_blank=True, trim_whitespace=True)
    bluesky_password = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                             trim_whitespace=True, write_only=True)
    poll_interval_minutes = serializers.IntegerField(required=False, min_value=MIN_INTERVAL, max_value=MAX_INTERVAL)

    def validate_bluesky_handle(self, value):
        value = value.lstrip("@")
        if any(c.isspace() for c in value):
            raise serializers.ValidationError("A Bluesky handle can't contain spaces.")
        return value

    def validate(self, attrs):
        if "discord_webhook_url" in self.initial_data:
            raise serializers.ValidationError({"discord_webhook_url": [
                "Discord channels are set up under Discord channels (/api/webhooks/), not here."]})
        return attrs

    def changes(self) -> dict:
        data = dict(self.validated_data)
        if not data.get("bluesky_password"):
            data.pop("bluesky_password", None)
        return data
