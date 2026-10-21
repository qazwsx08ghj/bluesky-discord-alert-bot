from django.shortcuts import render
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from rest_framework.decorators import api_view
from rest_framework.response import Response

from botconfig import store
from botconfig.secrets import is_masked, mask_webhook_url
from botconfig.serializers import SettingsUpdateSerializer
from discord_alerts.sender import send_discord_alert


def index(request):
    return render(request, "index.html")


def _settings_response(row):
    """Settings as the control panel sees them. Secrets are never included."""
    return {
        "bluesky_handle": row.bluesky_handle,
        "discord_webhook_url": mask_webhook_url(row.discord_webhook_url),
        "poll_interval_minutes": str(row.poll_interval_minutes),  # a string, as before step 3
        "has_bluesky_password": bool(row.bluesky_password),
        "has_discord_webhook_url": bool(row.discord_webhook_url),
        "settings_version": row.version,
        "updated_at": row.updated_at,
        "env_file_in_sync": row.env_mirror_ok,
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
