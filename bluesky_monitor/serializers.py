from django.db import transaction
from rest_framework import serializers

from discord_alerts import channels, mentions
from discord_alerts.models import DiscordWebhook
from . import account_status, profiles
from .models import POST, REPLY, REPOST, AccountDestination, MonitoredAccount, PostDelivery, SeenPost

ANNOUNCE_FIELDS = {"announce_posts": POST, "announce_reposts": REPOST, "announce_replies": REPLY}


class ChannelSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = DiscordWebhook
        fields = ["id", "name", "is_default"]
        ref_name = "AccountChannel"


class PostDeliverySerializer(serializers.ModelSerializer):
    """A post's notification to one channel. `status`: pending, sending, retry (waiting to try
    again at `next_attempt_at`), sent, failed (gave up), cancelled (the channel was removed
    from the account before it was sent). `role_id`: the role mentioned, or "" for none."""
    channel = ChannelSummarySerializer(read_only=True)

    class Meta:
        model = PostDelivery
        fields = ["channel", "role_id", "status", "attempts", "next_attempt_at", "sent_at"]


class SeenPostSerializer(serializers.ModelSerializer):
    """A post the bot has noticed (GET /api/accounts/{id}/posts/).

    `status` sums up its `deliveries` (one per channel): seeded (noted on the first check,
    never sent), pending (a channel hasn't got it yet), sent (every channel got it),
    partly_sent (some channels got it, some gave up), failed (no channel got it), skipped (no
    channel, notifications for that kind of post are off, or every delivery was cancelled).
    Posts sent before an upgrade to several channels have no `deliveries`.
    """
    deliveries = PostDeliverySerializer(many=True, read_only=True)

    class Meta:
        model = SeenPost
        fields = ["id", "post_uri", "post_url", "text", "kind", "status", "seen_at", "sent_at", "deliveries"]


class DestinationSerializer(serializers.Serializer):
    """One place an account's notifications go.

    `channel_id`: a channel id, or null for "the default channel" (it follows the default,
    also when the default changes later). `role_id`: the ID of a Discord role to mention in
    that channel, or "" for no mention. Read-only: `channel` (null when following the
    default) and `follows_default`.
    """
    channel_id = serializers.PrimaryKeyRelatedField(source="channel", queryset=DiscordWebhook.objects.all(),
                                                    allow_null=True)
    role_id = serializers.CharField(required=False, allow_blank=True, default="", max_length=64,
                                    help_text="A Discord Role ID (17 to 20 digits) or the <@&ID> form; "
                                              "\"\" for no mention. @everyone and @here are refused.")
    channel = ChannelSummarySerializer(read_only=True, allow_null=True)
    follows_default = serializers.SerializerMethodField()

    def get_follows_default(self, obj) -> bool:
        return obj.channel_id is None

    def validate_role_id(self, value):
        try:
            return mentions.clean_role_id(value)
        except mentions.InvalidRoleId as exc:
            raise serializers.ValidationError(exc.message, code="invalid_role_id")


class MonitoredAccountSerializer(serializers.ModelSerializer):
    """A watched account.

    Where its posts go: `destinations`, 1 to 10 of {channel_id, role_id} (see Destination).
    Saving replaces the whole list. A new account without `destinations` follows the default
    channel. Changing destinations or roles affects only posts noticed afterwards; unsent
    notifications to a channel that is removed are cancelled.
    What is announced: `announce_posts`, `announce_reposts`, `announce_replies` (defaults
    on / on / off). Changing them affects only posts noticed afterwards.

    Read-only status (see account_status.py): `state`, `state_label`, `state_message`,
    `checking_now`, `problem`, `first_check`, `last_checked_at`, `last_check_ok`,
    `last_success_at`, `last_error`.

    `handle` accepts "@Name.bsky.social", a bsky.app profile link or a DID, and is stored
    cleaned up. A handle that is already watched is refused with code `already_watched`.

    Older versions had `discord_webhook_url`, then `channel_id` / `channel` (now
    `destinations`), and the nested `seen_posts` list (use GET /api/accounts/{id}/posts/).
    """
    announce_posts = serializers.BooleanField(required=False)
    announce_reposts = serializers.BooleanField(required=False)
    announce_replies = serializers.BooleanField(required=False)
    destinations = DestinationSerializer(many=True, required=False)

    class Meta:
        model = MonitoredAccount
        fields = [
            "id",
            "handle",
            "display_name",
            "destinations",
            "is_active",
            "announce_posts",
            "announce_reposts",
            "announce_replies",
            "first_poll_done",
            "created_at",
        ]
        read_only_fields = ["first_poll_done", "created_at"]
        extra_kwargs = {"handle": {"validators": []}}  # uniqueness is checked after cleaning, below

    def to_representation(self, obj):
        data = super().to_representation(obj)
        for field, kind in ANNOUNCE_FIELDS.items():
            data[field] = obj.announces(kind)  # null (accounts added by older code) means the default
        data.update(account_status.describe(obj))
        return data

    def validate(self, attrs):
        if "discord_webhook_url" in self.initial_data:
            raise serializers.ValidationError({"discord_webhook_url": [
                "Choose the Discord channels with destinations (see /api/webhooks/); webhook URLs aren't accepted here."]})
        if "channel_id" in self.initial_data:
            raise serializers.ValidationError({"channel_id": [
                "Choose the Discord channels with destinations: [{\"channel_id\": …, \"role_id\": \"\"}]."]})
        return attrs

    def validate_destinations(self, items):
        if not items:
            raise serializers.ValidationError("Choose at least one channel to send to.", code="no_destinations")
        limit = AccountDestination.MAX_PER_ACCOUNT
        if len(items) > limit:
            raise serializers.ValidationError(f"An account can send to at most {limit} channels.",
                                              code="too_many_destinations")
        default = channels.default_channel()
        follows_default = any(item["channel"] is None for item in items)
        errors, seen = [{} for _ in items], set()
        for n, item in enumerate(items):
            channel = item["channel"]
            key = getattr(channel, "pk", None)
            if key in seen:
                errors[n]["channel_id"] = ["The default channel is already in the list." if channel is None
                                           else f"#{channel.name} is already in the list."]
            elif channel is not None and follows_default and default is not None and channel.pk == default.pk:
                errors[n]["channel_id"] = [f"#{channel.name} is the default channel, which is already in the list."]
            seen.add(key)
            target = channel if channel is not None else default
            if target is not None and mentions.is_server_id(item.get("role_id", ""), target.guild_id or ""):
                errors[n]["role_id"] = [mentions.SERVER_ID_REFUSED]
        if any(errors):
            raise serializers.ValidationError(errors)
        return items

    def validate_handle(self, value):
        norm = profiles.normalize_handle(value)
        if not norm.ok:
            raise serializers.ValidationError(norm.error, code="invalid_handle")
        existing = profiles.watched_account(norm.actor)
        if existing is not None and (self.instance is None or existing.pk != self.instance.pk):
            raise serializers.ValidationError(f"You're already watching @{norm.actor}.", code="already_watched")
        return norm.actor

    def create(self, validated_data):
        destinations = validated_data.pop("destinations", None) or [{"channel": None, "role_id": ""}]
        with transaction.atomic():
            account = super().create(validated_data)
            channels.set_destinations(account, destinations)
        return account

    def update(self, instance, validated_data):
        destinations = validated_data.pop("destinations", None)
        with transaction.atomic():
            account = super().update(instance, validated_data)
            if destinations is not None:
                channels.set_destinations(account, destinations)
        if hasattr(account, "_prefetched_objects_cache"):
            account._prefetched_objects_cache.pop("destinations", None)
        return account
