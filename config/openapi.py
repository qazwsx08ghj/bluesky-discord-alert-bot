"""The API contract: describes every endpoint for OpenAPI (docs/openapi.yaml).

Only the contract tools import this module (`manage.py api_contract` and the contract
tests). The running bot never does, so drf-spectacular is a development dependency
(requirements-dev.txt) and nothing here can change how the API behaves.

How it works: for each view, an OpenApiViewExtension hands drf-spectacular an annotated
copy of that view while the schema is generated. The real views stay untouched. Many
endpoints build their answers by hand, so their shapes are written out below as
documentation-only serializers; config/tests_openapi.py checks real responses against
the generated schema, so these descriptions can't silently drift from the code.

Changing the API: see CONTRIBUTING.md, "Changing the API".
"""
from django.conf import settings

# drf-spectacular reads its settings when it is first imported, so they are set first.
SPECTACULAR_SETTINGS = {
    "TITLE": "Bluesky → Discord Alert Bot API",
    "DESCRIPTION": (
        "The control panel's REST API. It is reachable only from the computer the bot runs on "
        "(127.0.0.1) and has no login. Every change (POST, PUT, PATCH, DELETE) needs the CSRF token "
        "from GET /api/csrf/ in the X-CSRFToken header, together with its csrftoken cookie, and a JSON "
        "body. Webhook URLs and the App Password are write-only. Background, security rules and "
        "error codes: docs/API.md."
    ),
    "VERSION": "1.0.0",  # the contract's own version; bump it when the API changes
    "OAS_VERSION": "3.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,  # write-only fields appear only in request schemas
    "SORT_OPERATIONS": True,
    "ENUM_GENERATE_CHOICE_DESCRIPTION": False,
    "SCHEMA_PATH_PREFIX": r"/api/",
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
        "config.openapi.add_change_protection",
    ],
    "ENUM_NAME_OVERRIDES": {},  # filled in below, once the choices exist
    "TAGS": [
        {"name": "status", "description": "Is the bot working? Health, status and the activity log."},
        {"name": "settings", "description": "The bot's Bluesky login and check interval."},
        {"name": "accounts", "description": "Watched Bluesky accounts, their checks and Check All."},
        {"name": "channels", "description": "Named Discord channels (webhooks)."},
        {"name": "bluesky", "description": "Looking up Bluesky profiles."},
        {"name": "security", "description": "The CSRF token for changes."},
    ],
}
settings.SPECTACULAR_SETTINGS = SPECTACULAR_SETTINGS

from drf_spectacular.extensions import OpenApiViewExtension  # noqa: E402
from drf_spectacular.utils import (  # noqa: E402
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import serializers  # noqa: E402

from activity.serializers import ActivityEventDetailSerializer, ActivityEventSerializer  # noqa: E402
from bluesky_monitor.serializers import MonitoredAccountSerializer, SeenPostSerializer  # noqa: E402
from discord_alerts.serializers import DiscordWebhookSerializer  # noqa: E402

# -- value lists ---------------------------------------------------------------------------------
FIXES = ["update_login", "update_webhook", "choose_channel", "check_handle", "restart_bot", "wait", "none",
         "add_account"]
ACCOUNT_STATES = ["checking", "paused", "error", "waiting_first_check", "active"]
BOT_STATES = ["running", "problem", "not_configured", "unavailable"]
CHECKER_STATES = ["running", "checking", "stalled", "stopped", "not_started"]
LOGIN_STATES = ["ok", "failed", "rate_limited", "not_configured", "unknown"]
JOB_STATUSES = ["queued", "running", "finished", "interrupted", "expired"]
JOB_RESULTS = ["ok", "partial", "failed", "nothing_to_check", "not_configured", "login_failed"]
ROW_RESULTS = ["waiting", "checking", "checked", "failed", "skipped", "already_checking"]
ROW_REASONS = ["paused", "login_failed", "not_configured", "time_limit", "stopped"]
CHECK_RESULTS = ["checked", "failed", "skipped", "already_checking"]
LAST_CHECK_RESULTS = ["ok", "partial", "failed", "not_configured", "interrupted"]
# "too_many_tests": what the API really returns when the test limit is reached (its error code is
# reused as the result). docs/API.md listed only the others; kept as is, documented here.
LOGIN_TEST_RESULTS = ["ok", "rejected", "app_password_needed", "rate_limited", "unavailable", "error", "invalid",
                      "too_many_tests"]
LOOKUP_RESULTS = ["found", "not_found", "unavailable", "invalid", "rate_limited", "error"]
HEALTH_STATUSES = ["ok", "updating", "starting", "error"]
SEVERITIES = ["error", "warning", "setup"]

SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"] = {
    "Fix": FIXES, "AccountState": ACCOUNT_STATES, "BotState": BOT_STATES, "CheckerState": CHECKER_STATES,
    "LoginState": LOGIN_STATES, "AccountProblemSeverity": ["error", "warning"], "JobKind": ["check_all"], "JobStatus": JOB_STATUSES, "JobResult": JOB_RESULTS,
    "JobAccountResult": ROW_RESULTS, "JobAccountReason": ROW_REASONS, "AccountCheckResult": CHECK_RESULTS,
    "LastCheckResult": LAST_CHECK_RESULTS, "LoginTestResult": LOGIN_TEST_RESULTS,
    "LookupResult": LOOKUP_RESULTS, "HealthStatus": HEALTH_STATUSES, "ProblemSeverity": SEVERITIES,
    "PostStatus": "bluesky_monitor.models.SeenPost.STATUS_CHOICES",
    "PostKind": "bluesky_monitor.models.KIND_CHOICES",
    "ActivityLevel": "activity.models.ActivityEvent.LEVELS",
}


def _nullable(field):
    """allow_null=True, also for the copies DRF makes of declared fields (they are rebuilt from _kwargs)."""
    field.allow_null = True
    field._kwargs["allow_null"] = True
    return field


# -- shared pieces -------------------------------------------------------------------------------
class ErrorSerializer(serializers.Serializer):
    """Every error (except form field errors): a stable `code` for programs and a plain-language
    `message` to show. Some errors add more fields."""
    code = serializers.CharField(help_text="Stable error code, e.g. csrf_failed, not_found, already_checking.")
    message = serializers.CharField(help_text="Plain-language explanation, safe to show.")
    detail = serializers.CharField(required=False, help_text="DRF's own text; kept for older clients.")
    fields = serializers.DictField(child=serializers.ListField(child=serializers.CharField()), required=False,
                                   help_text="Errors per field.")
    accounts = serializers.ListField(child=serializers.DictField(), required=False,
                                     help_text="Channel delete: the accounts still posting to the channel.")
    retry_after_seconds = serializers.IntegerField(required=False)


FIELD_ERRORS = OpenApiResponse(
    response={"type": "object", "additionalProperties": {"type": "array", "items": {"type": "string"}}},
    description="Form errors per field, e.g. {\"handle\": [\"You're already watching @a.bsky.social.\"]}. "
                "No `code`: this shape is kept from the first API version.")


def error(description):
    return OpenApiResponse(response=ErrorSerializer, description=description)


NOT_FOUND = error("Unknown id or address (code `not_found`).")


class ProblemSerializer(serializers.Serializer):
    """A failure explained in plain language. `fix` says where the panel can fix it."""
    category = serializers.CharField(help_text="Problem code, e.g. bluesky_login_rejected (docs/ACTIVITY_AND_STATUS.md).")
    title = serializers.CharField()
    explanation = serializers.CharField()
    fix = serializers.ChoiceField(choices=FIXES)


class AccountProblemSerializer(ProblemSerializer):
    severity = serializers.ChoiceField(choices=["error", "warning"],
                                       help_text="warning: usually clears by itself.")


class OptionalSeverityProblemSerializer(ProblemSerializer):
    severity = serializers.ChoiceField(choices=["error", "warning"], required=False)


class AccountRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    handle = serializers.CharField()


class ChannelRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class AccountChannelSerializer(ChannelRefSerializer):
    is_default = serializers.BooleanField()


class CurrentProblemSerializer(ProblemSerializer):
    code = serializers.CharField()
    severity = serializers.ChoiceField(choices=SEVERITIES)
    message = serializers.CharField()
    since = _nullable(serializers.DateTimeField())
    account = AccountRefSerializer(required=False)
    channel = ChannelRefSerializer(required=False)


# -- status --------------------------------------------------------------------------------------
class HealthSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=HEALTH_STATUSES)
    version = serializers.CharField()
    database = serializers.BooleanField()
    migrations_pending = _nullable(serializers.IntegerField())
    checker = _nullable(serializers.ChoiceField(choices=CHECKER_STATES))


class LastCheckSerializer(serializers.Serializer):
    started_at = _nullable(serializers.DateTimeField())
    finished_at = serializers.DateTimeField()
    result = _nullable(serializers.ChoiceField(choices=LAST_CHECK_RESULTS))
    message = serializers.CharField(allow_blank=True)
    problem = _nullable(ProblemSerializer())
    accounts_checked = _nullable(serializers.IntegerField())
    accounts_failed = _nullable(serializers.IntegerField())
    posts_sent = _nullable(serializers.IntegerField())


class SchedulerHealthSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=CHECKER_STATES)
    healthy = serializers.BooleanField()
    message = serializers.CharField()
    problem = _nullable(ProblemSerializer())
    heartbeat_at = _nullable(serializers.DateTimeField())
    heartbeat_age_seconds = _nullable(serializers.IntegerField())
    active_since = _nullable(serializers.DateTimeField())
    check_interval_minutes = serializers.IntegerField()
    checking_now = serializers.BooleanField()
    current_check_started_at = _nullable(serializers.DateTimeField())
    last_check = _nullable(LastCheckSerializer())
    next_check_at = _nullable(serializers.DateTimeField())


class BlueskyLoginSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=LOGIN_STATES, help_text="unknown: saved, not tried yet.")
    handle = serializers.CharField(allow_blank=True)
    problem = _nullable(ProblemSerializer())
    retry_after = _nullable(serializers.DateTimeField())


class SetupSerializer(serializers.Serializer):
    bluesky_login = serializers.BooleanField()
    discord_channel = serializers.BooleanField()
    accounts = serializers.BooleanField()
    complete = serializers.BooleanField()
    missing = CurrentProblemSerializer(many=True)


class StatusCountsSerializer(serializers.Serializer):
    accounts_total = serializers.IntegerField()
    accounts_active = serializers.IntegerField()
    channels = serializers.IntegerField()
    pending_deliveries = serializers.IntegerField()
    deliveries_waiting = serializers.IntegerField()
    deliveries_retrying = serializers.IntegerField()
    sent_last_24h = serializers.IntegerField()
    failed_sends_last_24h = serializers.IntegerField()


class ActivityRefSerializer(serializers.Serializer):
    id = _nullable(serializers.IntegerField())
    label = serializers.CharField(allow_blank=True)


class ActivityEventDocSerializer(ActivityEventSerializer):
    account = _nullable(ActivityRefSerializer(read_only=True))
    channel = _nullable(ActivityRefSerializer(read_only=True))
    problem = _nullable(ProblemSerializer(read_only=True))
    is_retry = serializers.BooleanField(read_only=True)
    detail = serializers.DictField(read_only=True, help_text="Extra facts, e.g. post_url, post_text, seeded_posts.")

    class Meta(ActivityEventSerializer.Meta):
        ref_name = "ActivityEvent"


class ActivityEventDetailDocSerializer(ActivityEventDocSerializer):
    class Meta(ActivityEventDetailSerializer.Meta):
        ref_name = "ActivityEventDetail"


class StatusSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=BOT_STATES)
    headline = serializers.CharField()
    message = serializers.CharField()
    checked_at = serializers.DateTimeField()
    scheduler = SchedulerHealthSerializer()
    bluesky_login = BlueskyLoginSerializer()
    setup = SetupSerializer()
    counts = StatusCountsSerializer()
    problems = CurrentProblemSerializer(many=True)
    recent_errors = ActivityEventDocSerializer(many=True)


class CsrfSerializer(serializers.Serializer):
    csrf_token = serializers.CharField()
    header_name = serializers.CharField()
    cookie_name = serializers.CharField()


# -- settings ------------------------------------------------------------------------------------
class SettingsSerializer(serializers.Serializer):
    bluesky_handle = serializers.CharField(allow_blank=True)
    has_bluesky_password = serializers.BooleanField(help_text="The App Password itself is never returned.")
    default_channel = _nullable(ChannelRefSerializer())
    poll_interval_minutes = serializers.IntegerField(min_value=1, max_value=60)
    settings_version = serializers.IntegerField()
    updated_at = _nullable(serializers.DateTimeField())
    bluesky_login = BlueskyLoginSerializer()


class SettingsSavedSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["saved"])
    settings = SettingsSerializer()


class SettingsChangeSerializer(serializers.Serializer):
    bluesky_handle = serializers.CharField(required=False, allow_blank=True, max_length=255)
    bluesky_password = serializers.CharField(required=False, allow_blank=True, max_length=255, write_only=True,
                                             help_text="Blank or left out: keep the saved one.")
    poll_interval_minutes = serializers.IntegerField(required=False, min_value=1, max_value=60)


class LoginTestCredentialsSerializer(serializers.Serializer):
    bluesky_handle = serializers.CharField(required=False, allow_blank=True,
                                           help_text="Blank or left out: the saved handle.")
    bluesky_password = serializers.CharField(required=False, allow_blank=True, write_only=True,
                                             help_text="Blank or left out: the saved App Password.")


class LoginTestAccountSerializer(serializers.Serializer):
    did = serializers.CharField(allow_blank=True)
    handle = serializers.CharField(allow_blank=True)


class LoginTestSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    result = serializers.ChoiceField(choices=LOGIN_TEST_RESULTS)
    code = serializers.CharField()
    message = serializers.CharField()
    saved = serializers.BooleanField(help_text="Always false: a test never saves anything.")
    problem = _nullable(ProblemSerializer(required=False))
    handle = serializers.CharField(required=False)
    account = _nullable(LoginTestAccountSerializer(required=False))
    used_saved_handle = serializers.BooleanField(required=False)
    used_saved_password = serializers.BooleanField(required=False)
    matches_saved = serializers.BooleanField(required=False)
    password_looks_like_app_password = serializers.BooleanField(required=False)
    warning = _nullable(serializers.CharField(required=False))
    retry_after_seconds = _nullable(serializers.IntegerField(required=False))
    fields = serializers.DictField(child=serializers.ListField(child=serializers.CharField()), required=False)


# -- accounts ------------------------------------------------------------------------------------
class FirstCheckSerializer(serializers.Serializer):
    done = serializers.BooleanField()
    at = _nullable(serializers.DateTimeField())
    seeded_posts = _nullable(serializers.IntegerField(help_text="Posts noted (never sent) on the first check."))


class LastErrorSerializer(AccountProblemSerializer):
    at = _nullable(serializers.DateTimeField())
    ongoing = serializers.BooleanField()


class AccountSerializer(MonitoredAccountSerializer):
    """A watched account, with its current status (bluesky_monitor/account_status.py)."""
    channel = _nullable(AccountChannelSerializer(read_only=True))
    uses_default_channel = serializers.BooleanField(read_only=True)
    state = serializers.ChoiceField(choices=ACCOUNT_STATES, read_only=True)
    state_label = serializers.CharField(read_only=True)
    state_message = serializers.CharField(read_only=True)
    checking_now = serializers.BooleanField(read_only=True)
    problem = _nullable(AccountProblemSerializer(read_only=True))
    first_check = FirstCheckSerializer(read_only=True)
    last_checked_at = _nullable(serializers.DateTimeField(read_only=True))
    last_check_ok = _nullable(serializers.BooleanField(read_only=True))
    last_success_at = _nullable(serializers.DateTimeField(read_only=True))
    last_error = _nullable(LastErrorSerializer(read_only=True))

    class Meta(MonitoredAccountSerializer.Meta):
        fields = MonitoredAccountSerializer.Meta.fields + [
            "state", "state_label", "state_message", "checking_now", "problem", "first_check",
            "last_checked_at", "last_check_ok", "last_success_at", "last_error"]
        ref_name = "Account"


class CheckSummarySerializer(serializers.Serializer):
    result = serializers.ChoiceField(choices=CHECK_RESULTS)
    message = serializers.CharField()
    new_posts = serializers.IntegerField()
    posts_sent = serializers.IntegerField()
    not_announced = serializers.IntegerField()


class PollResultSerializer(serializers.Serializer):
    new_posts_announced = serializers.IntegerField()
    was_first_poll = serializers.BooleanField()
    state = serializers.ChoiceField(choices=ACCOUNT_STATES)
    state_label = serializers.CharField()
    state_message = serializers.CharField()
    first_check = FirstCheckSerializer()
    problem = _nullable(OptionalSeverityProblemSerializer())
    check = CheckSummarySerializer(required=False)
    error = serializers.CharField(required=False, help_text="Set when the bot's own Bluesky login didn't work.")


class PostSerializer(SeenPostSerializer):
    class Meta(SeenPostSerializer.Meta):
        ref_name = "Post"


# -- check jobs ----------------------------------------------------------------------------------
class JobProgressCurrentSerializer(AccountRefSerializer):
    pass


class JobProgressSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    done = serializers.IntegerField()
    percent = serializers.IntegerField()
    current = _nullable(JobProgressCurrentSerializer())


class JobCountsSerializer(serializers.Serializer):
    checked = serializers.IntegerField()
    failed = serializers.IntegerField()
    skipped = serializers.IntegerField()
    already_checking = serializers.IntegerField()
    waiting = serializers.IntegerField()
    new_posts = serializers.IntegerField()
    posts_sent = serializers.IntegerField()


class JobAccountSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    handle = serializers.CharField()
    result = serializers.ChoiceField(choices=ROW_RESULTS)
    reason = _nullable(serializers.ChoiceField(choices=ROW_REASONS))
    message = serializers.CharField()
    problem = _nullable(ProblemSerializer())
    first_check = _nullable(serializers.BooleanField())
    new_posts = serializers.IntegerField()
    posts_sent = serializers.IntegerField()
    not_announced = serializers.IntegerField()
    state = _nullable(serializers.ChoiceField(choices=ACCOUNT_STATES))
    state_label = _nullable(serializers.CharField())


class CheckJobSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=["check_all"])
    url = serializers.CharField()
    status = serializers.ChoiceField(choices=JOB_STATUSES)
    final = serializers.BooleanField()
    status_label = serializers.CharField()
    message = serializers.CharField(allow_blank=True)
    result = _nullable(serializers.ChoiceField(choices=JOB_RESULTS))
    problem = _nullable(ProblemSerializer())
    requested_at = serializers.DateTimeField()
    started_at = _nullable(serializers.DateTimeField())
    finished_at = _nullable(serializers.DateTimeField())
    updated_at = _nullable(serializers.DateTimeField())
    progress = JobProgressSerializer()
    counts = JobCountsSerializer()
    accounts = JobAccountSerializer(many=True)
    checker_running = _nullable(serializers.BooleanField())
    poll_after_seconds = _nullable(serializers.FloatField(help_text="Ask again after this long; null once final."))


class CheckJobStartedSerializer(CheckJobSerializer):
    created = serializers.BooleanField(help_text="false: a Check All was already queued or running; this is that job.")


class CheckJobListSerializer(serializers.Serializer):
    results = CheckJobSerializer(many=True)


# -- channels ------------------------------------------------------------------------------------
class LastDeliverySerializer(serializers.Serializer):
    at = serializers.DateTimeField()
    ok = _nullable(serializers.BooleanField())
    error = serializers.CharField(allow_blank=True)


class ChannelSerializer(DiscordWebhookSerializer):
    url_masked = serializers.CharField(read_only=True, help_text="e.g. https://discord.com/api/webhooks/1234/****abcd")
    account_count = serializers.IntegerField(read_only=True)
    last_delivery = _nullable(LastDeliverySerializer(read_only=True))

    class Meta(DiscordWebhookSerializer.Meta):
        ref_name = "Channel"


class SentSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["sent", "failed"])


# -- profile lookup ------------------------------------------------------------------------------
class ProfileSerializer(serializers.Serializer):
    did = serializers.CharField()
    handle = serializers.CharField()
    display_name = serializers.CharField(allow_blank=True)
    avatar_url = _nullable(serializers.CharField())
    description = serializers.CharField(allow_blank=True)
    followers_count = _nullable(serializers.IntegerField())
    posts_count = _nullable(serializers.IntegerField())
    watch_as = serializers.CharField(help_text="What to send as `handle` when adding the account.")


class ProfileLookupSerializer(serializers.Serializer):
    query = serializers.CharField(allow_blank=True)
    handle = serializers.CharField(allow_blank=True)
    result = serializers.ChoiceField(choices=LOOKUP_RESULTS)
    exists = _nullable(serializers.BooleanField())
    profile = _nullable(ProfileSerializer())
    already_watched = serializers.BooleanField()
    watched_account_id = _nullable(serializers.IntegerField())
    code = serializers.CharField(allow_blank=True)
    message = serializers.CharField(allow_blank=True)
    problem = _nullable(ProblemSerializer())
    suggestion = _nullable(serializers.CharField())
    retryable = serializers.BooleanField()
    retry_after_seconds = _nullable(serializers.IntegerField())
    cached = serializers.BooleanField()


# -- schema-wide additions -----------------------------------------------------------------------
CHANGE_ERRORS = {
    "403": {"description": "Refused: missing or wrong CSRF token (code `csrf_failed`), or sent from another "
                           "website (code `cross_site_request`).",
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
    "415": {"description": "The body isn't JSON (code `json_required`).",
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
}


def add_change_protection(result, generator, request, public):
    """Every change needs the CSRF token and a JSON body; say so on every such operation."""
    result.setdefault("components", {}).setdefault("securitySchemes", {})["csrfToken"] = {
        "type": "apiKey", "in": "header", "name": "X-CSRFToken",
        "description": "The token from GET /api/csrf/, sent with its csrftoken cookie.",
    }
    for path in result.get("paths", {}).values():
        for method, operation in path.items():
            if method in ("post", "put", "patch", "delete"):
                operation["security"] = [{"csrfToken": []}]
                for code, response in CHANGE_ERRORS.items():
                    operation.setdefault("responses", {}).setdefault(code, response)
                if "requestBody" in operation:
                    _allow_invalid_json(operation["responses"])
            else:
                operation["security"] = [{}]
    _nullable_for_openapi_31(result)
    for name in ALWAYS_COMPLETE:  # optional when sent, but always present in answers
        schema = result["components"]["schemas"][name]
        schema["required"] = sorted(schema["properties"])
    _drop_unused_schemas(result)
    return result


# Response schemas built from model serializers: every listed field is always in the answer
# (fields that are optional only apply to requests, which have their own *Request schemas).
ALWAYS_COMPLETE = ("Account", "Channel", "Post", "ActivityEvent", "ActivityEventDetail")


def _nullable_for_openapi_31(node):
    """DRF's pagination describes next/previous with OpenAPI 3.0's `nullable: true`; in 3.1 that is
    the type list ["string", "null"]."""
    if isinstance(node, dict):
        if node.pop("nullable", False) and isinstance(node.get("type"), str):
            node["type"] = [node["type"], "null"]
        for child in node.values():
            _nullable_for_openapi_31(child)
    elif isinstance(node, list):
        for child in node:
            _nullable_for_openapi_31(child)


def _allow_invalid_json(responses):
    """A body that isn't valid JSON is answered with the standard error (code `invalid_json`),
    so a documented 400 of another shape is "that shape, or the standard error"."""
    error_ref = {"$ref": "#/components/schemas/Error"}
    note = " Or the standard error with code `invalid_json` when the body isn't valid JSON."
    if "400" not in responses:
        responses["400"] = {"description": "The body isn't valid JSON (code `invalid_json`).",
                            "content": {"application/json": {"schema": error_ref}}}
        return
    content = responses["400"].setdefault("content", {}).setdefault("application/json", {})
    schema = content.get("schema")
    if schema != error_ref:
        content["schema"] = {"oneOf": [schema, error_ref]}
        responses["400"]["description"] = (responses["400"].get("description") or "") + note


def _drop_unused_schemas(result):
    """Read-only nested parts also get request versions that nothing uses; leave them out."""
    import json
    import re

    schemas = result.get("components", {}).get("schemas", {})
    while True:
        dump = lambda value: json.dumps(value, default=str)  # noqa: E731 (lazy translation strings)
        text = dump({k: v for k, v in result.items() if k != "components"}) + dump(
            {k: v for k, v in result["components"].items() if k != "schemas"})
        used = set(re.findall(r"#/components/schemas/([A-Za-z0-9_]+)", text + dump(schemas)))
        unused = [name for name in schemas if name not in used]
        if not unused:
            return
        for name in unused:
            del schemas[name]


# -- the annotated views -------------------------------------------------------------------------
class _Replace(OpenApiViewExtension):
    """Base: subclasses set target_class and `annotate`, a function that decorates a copy of the view."""
    annotate = None

    def view_replacement(self):
        class Annotated(self.target_class):
            pass
        Annotated.__name__ = self.target_class.__name__
        return type(self).annotate(Annotated)


def _view(target, **schema):
    """An extension for a function view (@api_view)."""
    return type(f"Doc_{target.rsplit('.', 1)[1]}", (_Replace,), {
        "target_class": target, "annotate": staticmethod(extend_schema_view(**schema))})


_view("config.health.health", get=extend_schema(
    tags=["status"], operation_id="health", summary="Is the panel ready?",
    description="200 when the database answers and is fully migrated; otherwise 503 with the same body. "
                "Used by Docker and the Windows scripts.",
    responses={200: HealthSerializer, 503: OpenApiResponse(HealthSerializer, description="Starting or updating.")}))

_view("config.security.csrf_token", get=extend_schema(
    tags=["security"], operation_id="csrf_token", summary="Get the CSRF token",
    description="Sets the csrftoken cookie and returns the same token. Send it in X-CSRFToken on every change.",
    responses={200: CsrfSerializer}))

_view("activity.views.status_view", get=extend_schema(
    tags=["status"], operation_id="status", summary="The bot's status",
    description="Overall state, checker health, Bluesky login, setup progress, counts and current problems.",
    responses={200: StatusSerializer}))

_view("config.frontend_views.bot_settings",
      get=extend_schema(tags=["settings"], operation_id="settings_retrieve", summary="The settings",
                        responses={200: SettingsSerializer}),
      post=extend_schema(tags=["settings"], operation_id="settings_save",
                         summary="Change settings (same as PATCH)", request=SettingsChangeSerializer,
                         responses={200: SettingsSavedSerializer,
                                    400: error("`invalid_settings`, with `fields`. Nothing was saved."),
                                    503: error("`settings_not_saved`: the database couldn't be written.")}),
      patch=extend_schema(tags=["settings"], operation_id="settings_update", summary="Change settings",
                          description="Applies at once, no restart. Discord channels are under /api/webhooks/; "
                                      "sending the removed `discord_webhook_url` field is refused (400).",
                          request=SettingsChangeSerializer,
                          responses={200: SettingsSavedSerializer,
                                     400: error("`invalid_settings`, with `fields`. Nothing was saved."),
                                     503: error("`settings_not_saved`: the database couldn't be written.")}))

_view("config.frontend_views.test_login", post=extend_schema(
    tags=["settings"], operation_id="settings_test_login", summary="Test a Bluesky login",
    description="Signs in once with a throwaway session; never saves anything. At most 5 tests per 5 minutes. "
                "A rejected login is a 200 with result=rejected.",
    request=LoginTestCredentialsSerializer,
    responses={200: LoginTestSerializer,
               400: OpenApiResponse(LoginTestSerializer, description="Nothing to test (result=invalid)."),
               429: OpenApiResponse(LoginTestSerializer, description="Too many tests, or Bluesky is limiting "
                                                                     "sign-ins. Retry-After header."),
               502: OpenApiResponse(LoginTestSerializer, description="Bluesky answered with an error."),
               503: OpenApiResponse(LoginTestSerializer, description="Bluesky couldn't be reached.")}))

_view("bluesky_monitor.views.profile_lookup", get=extend_schema(
    tags=["bluesky"], operation_id="bluesky_profile", summary="Look up a Bluesky profile",
    description="Uses Bluesky's public API (no login). The same body for every outcome; a missing profile is "
                "a 200 with result=not_found. For search-as-you-type, ignore answers whose `query` isn't the "
                "current text.",
    parameters=[OpenApiParameter("handle", str, required=True,
                                 description="A handle, @handle, bsky.app profile link or DID.")],
    responses={200: ProfileLookupSerializer,
               400: OpenApiResponse(ProfileLookupSerializer, description="Not a valid handle (result=invalid)."),
               429: OpenApiResponse(ProfileLookupSerializer, description="Bluesky is limiting requests."),
               502: OpenApiResponse(ProfileLookupSerializer, description="Bluesky answered with an error."),
               503: OpenApiResponse(ProfileLookupSerializer, description="Bluesky couldn't be reached.")}))

_view("bluesky_monitor.views.check_job_list", get=extend_schema(
    tags=["accounts"], operation_id="check_jobs_list", summary="Recent Check All jobs",
    description="Newest first. An empty list before the first Check All.",
    parameters=[OpenApiParameter("limit", int, description="1–50, default 10.")],
    responses={200: CheckJobListSerializer}))

_view("bluesky_monitor.views.check_job_latest", get=extend_schema(
    tags=["accounts"], operation_id="check_jobs_latest", summary="The newest Check All job",
    responses={200: CheckJobSerializer, 404: error("`no_check_jobs`: Check All hasn't been used yet.")}))

_view("bluesky_monitor.views.check_job_detail", get=extend_schema(
    tags=["accounts"], operation_id="check_jobs_retrieve", summary="One Check All job",
    description="Poll every `poll_after_seconds` until `final`.",
    responses={200: CheckJobSerializer, 404: error("`check_job_not_found`.")}))


class AccountsDoc(_Replace):
    target_class = "bluesky_monitor.views.MonitoredAccountViewSet"
    annotate = staticmethod(extend_schema_view(
        list=extend_schema(tags=["accounts"], summary="Watched accounts, newest first"),
        create=extend_schema(tags=["accounts"], summary="Watch an account",
                             description="`handle` accepts @handle, a bsky.app link or a DID. Sending the removed "
                                         "`discord_webhook_url` field is refused; use `channel_id`.",
                             responses={201: AccountSerializer, 400: FIELD_ERRORS}),
        retrieve=extend_schema(tags=["accounts"], summary="One account", responses={200: AccountSerializer,
                                                                                    404: NOT_FOUND}),
        update=extend_schema(tags=["accounts"], summary="Replace an account's settings",
                             responses={200: AccountSerializer, 400: FIELD_ERRORS, 404: NOT_FOUND}),
        partial_update=extend_schema(tags=["accounts"], summary="Change an account (pause, channel, name, …)",
                                     responses={200: AccountSerializer, 400: FIELD_ERRORS, 404: NOT_FOUND}),
        destroy=extend_schema(tags=["accounts"], summary="Stop watching an account",
                              responses={204: None, 404: NOT_FOUND}),
        poll_now=extend_schema(tags=["accounts"], summary="Check one account now",
                               description="Runs inside the request (a few seconds).", request=None,
                               responses={200: PollResultSerializer, 404: NOT_FOUND,
                                          409: error("`already_checking`.")}),
        posts=extend_schema(tags=["accounts"], summary="Posts noticed for an account, newest first",
                            responses={200: PostSerializer(many=True), 404: NOT_FOUND}),
        check_all=extend_schema(tags=["accounts"], summary="Check every account in the background",
                                description="Returns the job (also when one was already running: created=false). "
                                            "Location header: the job's address.", request=None,
                                responses={202: CheckJobStartedSerializer}),
    ))

    def view_replacement(self):
        view = super().view_replacement()
        view.serializer_class = AccountSerializer
        return view


class ChannelsDoc(_Replace):
    target_class = "discord_alerts.views.DiscordWebhookViewSet"
    annotate = staticmethod(extend_schema_view(
        list=extend_schema(tags=["channels"], summary="Discord channels"),
        create=extend_schema(tags=["channels"], summary="Add a channel",
                             responses={201: ChannelSerializer, 400: FIELD_ERRORS}),
        retrieve=extend_schema(tags=["channels"], summary="One channel",
                               responses={200: ChannelSerializer, 404: NOT_FOUND}),
        update=extend_schema(tags=["channels"], summary="Replace a channel",
                             description="Leave `url` out, or send the masked value back, to keep the saved URL.",
                             responses={200: ChannelSerializer, 400: FIELD_ERRORS, 404: NOT_FOUND}),
        partial_update=extend_schema(tags=["channels"], summary="Change a channel",
                                     description="Leave `url` out, or send the masked value back, to keep it.",
                                     responses={200: ChannelSerializer, 400: FIELD_ERRORS, 404: NOT_FOUND}),
        destroy=extend_schema(
            tags=["channels"], summary="Delete a channel",
            parameters=[OpenApiParameter("reassign_to", str, description="Where its accounts go: a channel id, "
                                                                          "or `default`.")],
            responses={204: None, 400: error("`invalid_reassign`."), 404: NOT_FOUND,
                       409: error("`channel_in_use` (with `accounts`), `channel_is_default` or "
                                  "`no_default_channel`.")}),
        make_default=extend_schema(tags=["channels"], summary="Make this the default channel", request=None,
                                   responses={200: ChannelSerializer, 404: NOT_FOUND}),
        test=extend_schema(tags=["channels"], summary="Send a test message", request=None,
                           responses={200: SentSerializer,
                                      502: OpenApiResponse(SentSerializer, description="Discord didn't accept it."),
                                      404: NOT_FOUND}),
    ))

    def view_replacement(self):
        view = super().view_replacement()
        view.serializer_class = ChannelSerializer
        return view


ACTIVITY_FILTERS = [
    OpenApiParameter("group", str, enum=["all", "sent", "failed_checks", "failed_sends", "retries", "problems"]),
    OpenApiParameter("type", str, description="Event types, comma-separated, e.g. post_sent,send_failed."),
    OpenApiParameter("level", str, description="success, info, warning, error; comma-separated."),
    OpenApiParameter("account", int), OpenApiParameter("channel", int),
    OpenApiParameter("since", str, description="A date (2026-10-21) or a time (2026-10-21T14:30:00Z)."),
    OpenApiParameter("before", str, description="A date or a time."),
]


class ActivityDoc(_Replace):
    target_class = "activity.views.ActivityViewSet"
    annotate = staticmethod(extend_schema_view(
        list=extend_schema(tags=["status"], summary="The activity log, newest first",
                           description="Cursor-paged: follow `next`.",
                           parameters=ACTIVITY_FILTERS,
                           responses={200: ActivityEventDocSerializer(many=True),
                                      400: error("`invalid_filter`, with `fields`.")}),
        retrieve=extend_schema(tags=["status"], summary="One activity entry, with `technical`",
                               responses={200: ActivityEventDetailDocSerializer, 404: NOT_FOUND}),
    ))

    def view_replacement(self):
        view = super().view_replacement()
        view.serializer_class = ActivityEventDocSerializer
        view.get_serializer_class = lambda self: (ActivityEventDetailDocSerializer if self.action == "retrieve"
                                                  else ActivityEventDocSerializer)
        return view


# -- generating the contract ---------------------------------------------------------------------
def generate():
    """The contract as YAML text, and whether drf-spectacular reported any warning or error.

    The schema class is switched on only for this call; the running bot never uses it.
    """
    from django.test.utils import override_settings
    from drf_spectacular.drainage import GENERATOR_STATS
    from drf_spectacular.generators import SchemaGenerator
    from drf_spectacular.renderers import OpenApiYamlRenderer
    from drf_spectacular.validation import validate_schema

    GENERATOR_STATS._warn_cache.clear()
    GENERATOR_STATS._error_cache.clear()
    rest = {**settings.REST_FRAMEWORK, "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema"}
    with override_settings(REST_FRAMEWORK=rest):
        schema = SchemaGenerator().get_schema(request=None, public=True)
    validate_schema(schema)
    text = OpenApiYamlRenderer().render(schema, renderer_context={}).decode("utf-8")
    return text.replace("\r\n", "\n"), bool(GENERATOR_STATS)
