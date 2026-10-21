import time

from django.conf import settings
from django.core.cache import cache
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from rest_framework.decorators import api_view
from rest_framework.response import Response

from activity import problems, status as bot_status
from bluesky_monitor import bluesky
from bluesky_monitor.models import BlueskySessionState
from botconfig import store
from botconfig.secrets import is_masked, mask_webhook_url
from botconfig.serializers import SettingsUpdateSerializer
from discord_alerts import channels
from discord_alerts.sender import send_discord_alert


def index(request):
    return render(request, "index.html")


def _settings_response(row):
    """Settings as the control panel sees them. Secrets are never included."""
    default = channels.default_channel()
    return {
        "bluesky_handle": row.bluesky_handle,
        "discord_webhook_url": mask_webhook_url(default.url) if default else "",
        "default_channel": {"id": default.id, "name": default.name} if default else None,
        "poll_interval_minutes": str(row.poll_interval_minutes),  # a string, as before step 3
        "has_bluesky_password": bool(row.bluesky_password),
        "has_discord_webhook_url": default is not None,
        "settings_version": row.version,
        "updated_at": row.updated_at,
        "env_file_in_sync": row.env_mirror_ok,
        # Step 6: whether the saved login works, as the checker last saw it
        # (ok, failed, rate_limited, not_configured, or unknown = not tried yet). Same as /api/status/.
        "bluesky_login": bot_status.bluesky_login(timezone.now()),
    }


@sensitive_post_parameters()
@api_view(["GET", "POST", "PATCH"])
@sensitive_variables("serializer")
def bot_settings(request):
    if request.method == "GET":
        return Response(_settings_response(store.get()))

    serializer = SettingsUpdateSerializer(data=request.data)
    if not serializer.is_valid():
        return Response({"code": "invalid_settings", "message": "Some settings weren't valid. Nothing was saved.",
                         "fields": serializer.errors}, status=400)
    try:
        result = store.save(serializer.changes())
        result.settings.refresh_from_db()
    except store.SettingsSaveError as exc:
        return Response({"code": "settings_not_saved", "message": str(exc)}, status=503)

    body = {"status": "saved", "settings": _settings_response(result.settings)}
    if not result.env_file_ok:
        body["warning"] = ("Saved and in use. The copy in the .env file couldn't be updated; "
                           "the bot will try again automatically.")
    return Response(body)


@api_view(["POST"])
@sensitive_variables("url", "saved")
def test_discord(request):
    saved = store.default_webhook_url()
    url = (request.data.get("url") or "").strip()
    if not url or is_masked(url):  # the masked URL from GET means "the saved one"
        url = saved
    if not url:
        return Response({"status": "error", "message": "No webhook URL configured"}, status=400)
    ok = send_discord_alert(
        webhook_url=url,
        handle="bluesky.bot",
        display_name="Bluesky Bot",
        text="This is a test message from your Bluesky → Discord Alert Bot!",
        post_url="https://bsky.app",
    )
    if ok:
        return Response({"status": "sent"})
    return Response({"status": "failed", "message": "Webhook request failed — check the URL"}, status=502)


# -- POST /api/settings/test-login/ ---------------------------------------------------------------
LOGIN_TEST_KEY = "login-test-times"

LOGIN_TEST_MESSAGES = {
    "rejected": "Bluesky didn't accept this handle and App Password. Check both and try again.",
    "app_password_needed": problems.CATEGORIES["bluesky_app_password_needed"].explanation,
    "rate_limited": "Bluesky is limiting sign-ins for this account right now. Wait a few minutes and try again.",
    "unavailable": "Couldn't reach Bluesky. Check the internet connection and try again.",
    "error": "Bluesky answered with an unexpected error. Try again in a moment.",
}
LOGIN_TEST_HTTP = {"ok": 200, "rejected": 200, "app_password_needed": 200, "rate_limited": 429,
                   "unavailable": 503, "error": 502}


def _login_tests_left(now: float):
    """At most LOGIN_TEST_LIMIT tests per LOGIN_TEST_WINDOW_SECONDS, so repeated clicks can't use up
    Bluesky's sign-in limit, which the bot itself also needs. Returns (allowed, retry_after_seconds)."""
    window = settings.LOGIN_TEST_WINDOW_SECONDS
    times = [t for t in cache.get(LOGIN_TEST_KEY, []) if t > now - window]
    if len(times) >= settings.LOGIN_TEST_LIMIT:
        return False, max(1, int(times[0] + window - now))
    cache.set(LOGIN_TEST_KEY, times + [now], window)
    return True, None


def _error(code, message, http_status, **extra):
    return Response({"ok": False, "result": "invalid" if http_status == 400 else code, "code": code,
                     "message": message, "saved": False, **extra}, status=http_status)


@sensitive_post_parameters()
@api_view(["POST"])
@sensitive_variables("password", "saved_password", "data", "raw_password")
def test_login(request):
    """Try a Bluesky handle and App Password without saving anything.

    Body (both optional): `bluesky_handle`, `bluesky_password`. A missing handle or a blank
    password means "use the saved one", so the saved login can be tested without retyping it.
    The saved settings, the saved session and the password are never changed or returned.
    If the *saved* login is tested and works, a wait after earlier failures is ended so the
    next check signs in straight away.
    """
    data = request.data if hasattr(request.data, "get") else {}
    raw_handle, raw_password = data.get("bluesky_handle"), data.get("bluesky_password")
    if not isinstance(raw_handle, (str, type(None))) or not isinstance(raw_password, (str, type(None))):
        return _error("invalid_login_test", "Send the handle and password as text.", 400)
    saved_handle, saved_password = store.credentials()

    handle = (raw_handle or "").strip().lstrip("@").strip() or saved_handle
    password = (raw_password or "").strip() or saved_password
    used_saved_handle = not (raw_handle or "").strip()
    used_saved_password = not (raw_password or "").strip()
    if not handle or not password:
        missing = {k: ["Required."] for k, v in (("bluesky_handle", handle), ("bluesky_password", password)) if not v}
        return _error("bluesky_not_configured", "Enter the bot's Bluesky handle and App Password (or save them "
                      "first), then test again.", 400, fields=missing)
    if any(c.isspace() for c in handle) or len(handle) > 255 or len(password) > 255:
        return _error("invalid_login_test", "A Bluesky handle can't contain spaces.", 400,
                      fields={"bluesky_handle": ["A Bluesky handle can't contain spaces."]})

    # Bluesky is already limiting this account's sign-ins: don't add to it.
    state = BlueskySessionState.objects.filter(pk=1).first()
    now = timezone.now()
    if (state and state.login_retry_after and state.login_retry_after > now and not state.login_failures
            and state.handle.lower() == handle.lower()):
        wait = max(1, int((state.login_retry_after - now).total_seconds()))
        return _login_response(bluesky.LoginTest("rate_limited", "bluesky_rate_limited", retry_after=wait),
                               handle, used_saved_handle, used_saved_password, False, password)

    allowed, wait = _login_tests_left(time.time())
    if not allowed:
        response = _error("too_many_tests", f"That's a lot of tests in a short time. Try again in {wait} seconds "
                          "(this protects the bot's own Bluesky sign-ins).", 429, retry_after_seconds=wait)
        response["Retry-After"] = str(wait)
        return response

    result = bluesky.try_login(handle, password)
    matches_saved = (bool(saved_handle and saved_password) and handle.lower() == saved_handle.lower()
                     and password == saved_password)
    if result.result == "ok" and matches_saved:
        bluesky.saved_login_works(saved_handle, saved_password)
    return _login_response(result, handle, used_saved_handle, used_saved_password, matches_saved, password)


@sensitive_variables("password")
def _login_response(result, handle, used_saved_handle, used_saved_password, matches_saved, password):
    ok = result.result == "ok"
    if ok:
        message = ("The saved login works." if matches_saved else
                   "The login works. It isn't saved yet: press Save to start using it.")
    elif result.category == "account_unavailable":
        message = "Bluesky says this account is deactivated or suspended, so the bot can't sign in with it."
    else:
        message = LOGIN_TEST_MESSAGES.get(result.result, LOGIN_TEST_MESSAGES["error"])
    app_shape = bluesky.looks_like_app_password(password)
    body = {
        "ok": ok,
        "result": result.result,
        "code": "login_ok" if ok else (result.category or "bluesky_error"),
        "message": message,
        "problem": problems.describe(result.category) if result.category else None,
        "handle": handle,
        "account": {"did": result.did, "handle": result.handle} if ok else None,
        "used_saved_handle": used_saved_handle,
        "used_saved_password": used_saved_password,
        "matches_saved": matches_saved,
        "saved": False,  # a test never saves anything
        "password_looks_like_app_password": app_shape,
        "warning": None if app_shape else ("This doesn't look like an App Password (xxxx-xxxx-xxxx-xxxx). Using an "
                                           "App Password instead of your main password is safer."),
        "retry_after_seconds": result.retry_after,
    }
    response = Response(body, status=LOGIN_TEST_HTTP.get(result.result, 502))
    if result.retry_after:
        response["Retry-After"] = str(result.retry_after)
    return response
