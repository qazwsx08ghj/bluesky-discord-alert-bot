from rest_framework import serializers

from . import problems
from .models import ActivityEvent


class ActivityEventSerializer(serializers.ModelSerializer):
    """One log line. `problem` explains a failure in plain language ({category, title,
    explanation, fix}); the technical cause is only in the single-event view."""
    account = serializers.SerializerMethodField()
    channel = serializers.SerializerMethodField()
    problem = serializers.SerializerMethodField()
    is_retry = serializers.SerializerMethodField()

    class Meta:
        model = ActivityEvent
        fields = ["id", "created_at", "last_seen_at", "repeat_count", "type", "level", "message",
                  "problem", "account", "channel", "attempt", "is_retry", "detail"]

    def get_account(self, obj):
        if obj.account_id is None and not obj.account_label:
            return None
        return {"id": obj.account_id, "label": obj.account_label}

    def get_channel(self, obj):
        if obj.channel_id is None and not obj.channel_label:
            return None
        return {"id": obj.channel_id, "label": obj.channel_label}

    def get_problem(self, obj):
        return problems.describe(obj.category)

    def get_is_retry(self, obj):
        return bool(obj.attempt and obj.attempt > 1)


class ActivityEventDetailSerializer(ActivityEventSerializer):
    """Adds `technical`: the internal cause (error type, HTTP status), for troubleshooting.
    Secrets were removed before it was stored."""

    class Meta(ActivityEventSerializer.Meta):
        fields = ActivityEventSerializer.Meta.fields + ["technical"]
