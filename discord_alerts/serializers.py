import re

from rest_framework import serializers

from botconfig.secrets import is_masked, mask_webhook_url
from . import channels
from .models import DiscordWebhook

DISCORD_WEBHOOK_URL = re.compile(
    r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9_\-]+/?$")
URL_HELP = "Paste the full webhook URL from Discord. It starts with https://discord.com/api/webhooks/"


def validate_webhook_url(value: str) -> str:
    """Accept only Discord webhook URLs. The error never repeats the value (it is a secret)."""
    value = (value or "").strip()
    if not DISCORD_WEBHOOK_URL.match(value):
        raise serializers.ValidationError(URL_HELP)
    return value


class DiscordWebhookSerializer(serializers.ModelSerializer):
    """A named channel. `url` is write-only; responses show `url_masked`.

    On edit, leave `url` out (or send the masked value back) to keep the saved URL.
    """
    url = serializers.CharField(write_only=True, required=False, max_length=500, trim_whitespace=True)
    url_masked = serializers.SerializerMethodField()
    # Declared explicitly: the single-default rule is applied by channels.make_default (one
    # transaction) and enforced by the database. DRF's generated check would reject every new default.
    is_default = serializers.BooleanField(required=False)
    account_count = serializers.SerializerMethodField()
    last_delivery = serializers.SerializerMethodField()

    class Meta:
        model = DiscordWebhook
        fields = ["id", "name", "url", "url_masked", "is_default", "created_at", "updated_at",
                  "account_count", "last_delivery"]
        read_only_fields = ["created_at", "updated_at"]

    def get_url_masked(self, obj):
        return mask_webhook_url(obj.url)

    def get_account_count(self, obj):
        count = getattr(obj, "account_count", None)
        return obj.accounts.count() if count is None else count

    def get_last_delivery(self, obj):
        if obj.last_delivery_at is None:
            return None
        return {"at": obj.last_delivery_at, "ok": obj.last_delivery_ok, "error": obj.last_delivery_error or ""}

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Give the channel a name.")
        others = DiscordWebhook.objects.exclude(pk=getattr(self.instance, "pk", None))
        if others.filter(name__iexact=value).exists():
            raise serializers.ValidationError("A channel with this name already exists.")
        return value

    def validate_url(self, value):
        if self.instance is not None and is_masked(value):
            return None  # the masked value shown by the API means "unchanged"
        return validate_webhook_url(value)

    def validate(self, attrs):
        if self.instance is None and not attrs.get("url"):
            raise serializers.ValidationError({"url": [URL_HELP]})
        if "url" in attrs and attrs["url"] is None:
            attrs.pop("url")
        if self.instance is not None and self.instance.is_default and attrs.get("is_default") is False:
            raise serializers.ValidationError({"is_default": [
                "This is the default channel. Make another channel the default instead."]})
        return attrs

    def create(self, validated_data):
        make_default = validated_data.pop("is_default", False)
        channel = DiscordWebhook.objects.create(**validated_data)
        if make_default:
            channels.make_default(channel)
        return channel

    def update(self, instance, validated_data):
        make_default = validated_data.pop("is_default", None)
        url_changed = "url" in validated_data and validated_data["url"] != instance.url
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.save(update_fields=[*validated_data.keys(), "updated_at"])
        if make_default and not instance.is_default:
            channels.make_default(instance)
        elif url_changed:
            channels.sync_rollback_copies()
        return instance
