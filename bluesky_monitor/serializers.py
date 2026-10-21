from rest_framework import serializers

from botconfig.secrets import is_masked, mask_webhook_url
from discord_alerts import channels
from discord_alerts.models import DiscordWebhook
from discord_alerts.serializers import validate_webhook_url
from . import account_status, profiles
from .models import POST, REPLY, REPOST, MonitoredAccount, SeenPost

ANNOUNCE_FIELDS = {"announce_posts": POST, "announce_reposts": REPOST, "announce_replies": REPLY}


class SeenPostSerializer(serializers.ModelSerializer):
    class Meta:
        model = SeenPost
        fields = ["id", "post_uri", "post_url", "text", "seen_at"]


class MonitoredAccountSerializer(serializers.ModelSerializer):
    """A watched account.

    Where its posts go:
    - `channel_id`: a channel id, or null to follow the default channel.
    - `discord_webhook_url` (kept from before step 4): a URL is matched to the channel with
      that URL or saved as a new one; "" means "follow the default"; the masked value shown
      in responses means "unchanged". Responses only ever show the masked URL.

    What is announced: `announce_posts`, `announce_reposts`, `announce_replies` (defaults
    on / on / off). Changing them affects only posts noticed afterwards.

    Read-only status (step 6, see account_status.py): `state`, `state_label`, `state_message`,
    `checking_now`, `problem`, `first_check`, `last_checked_at`, `last_check_ok`,
    `last_success_at`, `last_error`.

    `handle` accepts "@Name.bsky.social", a bsky.app profile link or a DID, and is stored
    cleaned up. A handle that is already watched is refused with code `already_watched`.
    """
    seen_posts = SeenPostSerializer(many=True, read_only=True)
    announce_posts = serializers.BooleanField(required=False)
    announce_reposts = serializers.BooleanField(required=False)
    announce_replies = serializers.BooleanField(required=False)
    channel_id = serializers.PrimaryKeyRelatedField(
        source="channel", queryset=DiscordWebhook.objects.all(), allow_null=True, required=False)
    channel = serializers.SerializerMethodField()
    uses_default_channel = serializers.SerializerMethodField()
    discord_webhook_url = serializers.CharField(required=False, allow_blank=True, max_length=500)

    class Meta:
        model = MonitoredAccount
        fields = [
            "id",
            "handle",
            "display_name",
            "discord_webhook_url",
            "channel_id",
            "channel",
            "uses_default_channel",
            "is_active",
            "announce_posts",
            "announce_reposts",
            "announce_replies",
            "first_poll_done",
            "created_at",
            "seen_posts",
        ]
        read_only_fields = ["first_poll_done", "created_at"]
        extra_kwargs = {"handle": {"validators": []}}  # uniqueness is checked after cleaning, below

    def get_channel(self, obj):
        if obj.channel is None:
            return None
        return {"id": obj.channel.id, "name": obj.channel.name, "is_default": obj.channel.is_default}

    def get_uses_default_channel(self, obj):
        return obj.channel_id is None

    def to_representation(self, obj):
        data = super().to_representation(obj)
        data["discord_webhook_url"] = mask_webhook_url(obj.channel.url) if obj.channel else ""
        for field, kind in ANNOUNCE_FIELDS.items():
            data[field] = obj.announces(kind)  # null (accounts added by older code) means the default
        data.update(account_status.describe(obj))
        return data

    def validate_handle(self, value):
        norm = profiles.normalize_handle(value)
        if not norm.ok:
            raise serializers.ValidationError(norm.error, code="invalid_handle")
        existing = profiles.watched_account(norm.actor)
        if existing is not None and (self.instance is None or existing.pk != self.instance.pk):
            raise serializers.ValidationError(f"You're already watching @{norm.actor}.", code="already_watched")
        return norm.actor

    def validate_discord_webhook_url(self, value):
        value = value.strip()
        if not value or is_masked(value):
            return value
        return validate_webhook_url(value)

    def _apply_legacy_url(self, account, legacy_url):
        if legacy_url is None or is_masked(legacy_url):
            return
        if legacy_url == "":
            account.channel = None
        else:
            account.channel = channels.channel_for_url(legacy_url, f"Channel for @{account.handle}")

    def create(self, validated_data):
        legacy_url = validated_data.pop("discord_webhook_url", None)
        account = MonitoredAccount(**validated_data)
        if "channel" not in validated_data:
            self._apply_legacy_url(account, legacy_url)
        account.save()
        channels.sync_rollback_copies()
        return account

    def update(self, instance, validated_data):
        legacy_url = validated_data.pop("discord_webhook_url", None)
        for key, value in validated_data.items():
            setattr(instance, key, value)
        if "channel" not in validated_data:
            self._apply_legacy_url(instance, legacy_url)
        instance.save()
        channels.sync_rollback_copies()
        return instance
