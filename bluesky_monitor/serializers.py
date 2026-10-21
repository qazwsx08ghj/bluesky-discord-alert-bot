from rest_framework import serializers

from discord_alerts.models import DiscordWebhook
from . import account_status, profiles
from .models import POST, REPLY, REPOST, MonitoredAccount, SeenPost

ANNOUNCE_FIELDS = {"announce_posts": POST, "announce_reposts": REPOST, "announce_replies": REPLY}


class SeenPostSerializer(serializers.ModelSerializer):
    """A post the bot has noticed (GET /api/accounts/{id}/posts/). `status`: seeded (noted on
    the first check, never sent), pending, sending, retry, sent, failed (gave up), skipped
    (no channel, or that kind of post isn't announced)."""

    class Meta:
        model = SeenPost
        fields = ["id", "post_uri", "post_url", "text", "kind", "status", "attempts", "seen_at", "sent_at"]


class MonitoredAccountSerializer(serializers.ModelSerializer):
    """A watched account.

    Where its posts go: `channel_id`, a channel id, or null to follow the default channel.
    What is announced: `announce_posts`, `announce_reposts`, `announce_replies` (defaults
    on / on / off). Changing them affects only posts noticed afterwards.

    Read-only status (see account_status.py): `state`, `state_label`, `state_message`,
    `checking_now`, `problem`, `first_check`, `last_checked_at`, `last_check_ok`,
    `last_success_at`, `last_error`.

    `handle` accepts "@Name.bsky.social", a bsky.app profile link or a DID, and is stored
    cleaned up. A handle that is already watched is refused with code `already_watched`.

    Step 10 removed `discord_webhook_url` (use `channel_id` / `channel`) and the nested
    `seen_posts` list (use GET /api/accounts/{id}/posts/, which is paged).
    """
    announce_posts = serializers.BooleanField(required=False)
    announce_reposts = serializers.BooleanField(required=False)
    announce_replies = serializers.BooleanField(required=False)
    channel_id = serializers.PrimaryKeyRelatedField(
        source="channel", queryset=DiscordWebhook.objects.all(), allow_null=True, required=False)
    channel = serializers.SerializerMethodField()
    uses_default_channel = serializers.SerializerMethodField()

    class Meta:
        model = MonitoredAccount
        fields = [
            "id",
            "handle",
            "display_name",
            "channel_id",
            "channel",
            "uses_default_channel",
            "is_active",
            "announce_posts",
            "announce_reposts",
            "announce_replies",
            "first_poll_done",
            "created_at",
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
        for field, kind in ANNOUNCE_FIELDS.items():
            data[field] = obj.announces(kind)  # null (accounts added by older code) means the default
        data.update(account_status.describe(obj))
        return data

    def validate(self, attrs):
        if "discord_webhook_url" in self.initial_data:
            raise serializers.ValidationError({"discord_webhook_url": [
                "Choose the Discord channel with channel_id (see /api/webhooks/); webhook URLs aren't accepted here."]})
        return attrs

    def validate_handle(self, value):
        norm = profiles.normalize_handle(value)
        if not norm.ok:
            raise serializers.ValidationError(norm.error, code="invalid_handle")
        existing = profiles.watched_account(norm.actor)
        if existing is not None and (self.instance is None or existing.pk != self.instance.pk):
            raise serializers.ValidationError(f"You're already watching @{norm.actor}.", code="already_watched")
        return norm.actor
