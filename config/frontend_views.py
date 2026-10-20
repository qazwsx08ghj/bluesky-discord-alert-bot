import re
from pathlib import Path
from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework.response import Response
from discord_alerts.sender import send_discord_alert

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _read_env():
    if not ENV_PATH.exists():
        return {}
    values = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip()
    return values


def _write_env_key(key: str, value: str):
    content = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    pattern = rf"^{re.escape(key)}=.*$"
    new_line = f"{key}={value}"
    if re.search(pattern, content, re.MULTILINE):
        content = re.sub(pattern, new_line, content, flags=re.MULTILINE)
    else:
        content = content.rstrip("\n") + f"\n{new_line}\n"
    ENV_PATH.write_text(content, encoding="utf-8")


def index(request):
    return render(request, "index.html")


@api_view(["GET", "POST"])
def bot_settings(request):
    if request.method == "GET":
        env = _read_env()
        return Response({
            "bluesky_handle": env.get("BLUESKY_HANDLE", ""),
            "discord_webhook_url": env.get("DISCORD_WEBHOOK_URL", ""),
            "poll_interval_minutes": env.get("POLL_INTERVAL_MINUTES", "5"),
            "has_bluesky_password": bool(env.get("BLUESKY_PASSWORD", "")),
        })

    data = request.data
    if "bluesky_handle" in data:
        _write_env_key("BLUESKY_HANDLE", data["bluesky_handle"])
    if data.get("bluesky_password"):
        _write_env_key("BLUESKY_PASSWORD", data["bluesky_password"])
    if "discord_webhook_url" in data:
        _write_env_key("DISCORD_WEBHOOK_URL", data["discord_webhook_url"])
    if "poll_interval_minutes" in data:
        _write_env_key("POLL_INTERVAL_MINUTES", str(data["poll_interval_minutes"]))

    return Response({"status": "saved"})


@api_view(["POST"])
def test_discord(request):
    url = request.data.get("url") or _read_env().get("DISCORD_WEBHOOK_URL", "")
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
