from datetime import datetime, time

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import viewsets
from rest_framework.decorators import api_view
from rest_framework.exceptions import APIException
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response

from . import log, status as bot_status
from .models import ActivityEvent
from .serializers import ActivityEventDetailSerializer, ActivityEventSerializer

# ?group= shortcuts for the activity log's filter tabs.
GROUPS = {
    "all": {},
    "sent": {"type__in": [log.POST_SENT]},
    "failed_checks": {"type__in": [log.CHECK_FAILED, log.LOGIN_FAILED, log.CYCLE_FAILED]},
    "failed_sends": {"type__in": [log.SEND_FAILED, log.SEND_GAVE_UP, log.POST_NOT_SENT, log.CHANNEL_TEST_FAILED]},
    "retries": {"attempt__gt": 1},
    "problems": {"level__in": [ActivityEvent.WARNING, ActivityEvent.ERROR]},
}
LEVELS = {value for value, _ in ActivityEvent.LEVELS}


class InvalidFilter(APIException):
    """400 {code: "invalid_filter", message, fields} (same shape as the settings errors)."""
    status_code = 400

    def __init__(self, field, message):
        super().__init__({"code": "invalid_filter", "message": message, "fields": {field: [message]}})


class ActivityPagination(CursorPagination):
    """Newest first. Cursor-based, so pages stay stable while new events arrive."""
    ordering = "-id"
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


def _datetime_param(params, name):
    raw = params.get(name)
    if not raw:
        return None
    value = parse_datetime(raw)
    if value is None:
        day = parse_date(raw)
        value = datetime.combine(day, time.min) if day else None
    if value is None:
        raise InvalidFilter(name, "Use a date like 2026-10-21 or a time like 2026-10-21T14:30:00Z.")
    return timezone.make_aware(value) if timezone.is_naive(value) else value


def _id_param(params, name):
    raw = params.get(name)
    if not raw:
        return None
    if not raw.isdigit():
        raise InvalidFilter(name, "Use the id number.")
    return int(raw)


def _list_param(params, name, allowed=None):
    raw = params.get(name)
    if not raw:
        return None
    values = [v.strip() for v in raw.split(",") if v.strip()]
    if allowed is not None and not set(values) <= allowed:
        raise InvalidFilter(name, f"Choose from: {', '.join(sorted(allowed))}.")
    return values


class ActivityViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/activity/ (newest first) and GET /api/activity/{id}/ (adds `technical`).

    Filters (all optional, combined with AND):
      group=all|sent|failed_checks|failed_sends|retries|problems
      type=post_sent,send_failed,...   level=success,info,warning,error
      account=<id>   channel=<id>   since=<date or time>   before=<date or time>
    Paging: page_size (50, at most 200); follow `next` / `previous` links.
    """
    queryset = ActivityEvent.objects.all()
    pagination_class = ActivityPagination

    def get_serializer_class(self):
        return ActivityEventDetailSerializer if self.action == "retrieve" else ActivityEventSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action != "list":
            return queryset
        params = self.request.query_params
        group = params.get("group") or "all"
        if group not in GROUPS:
            raise InvalidFilter("group", f"Choose from: {', '.join(GROUPS)}.")
        queryset = queryset.filter(**GROUPS[group])
        types = _list_param(params, "type")
        if types:
            queryset = queryset.filter(type__in=types)
        levels = _list_param(params, "level", LEVELS)
        if levels:
            queryset = queryset.filter(level__in=levels)
        account = _id_param(params, "account")
        if account is not None:
            queryset = queryset.filter(account_id=account)
        channel = _id_param(params, "channel")
        if channel is not None:
            queryset = queryset.filter(channel_id=channel)
        since, before = _datetime_param(params, "since"), _datetime_param(params, "before")
        if since:
            queryset = queryset.filter(created_at__gte=since)
        if before:
            queryset = queryset.filter(created_at__lt=before)
        return queryset


@api_view(["GET"])
def status_view(request):
    """The bot's health. See activity/status.py for how each state is decided."""
    return Response(bot_status.compute())
