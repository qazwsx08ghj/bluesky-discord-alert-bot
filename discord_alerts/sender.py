import logging
from dataclasses import dataclass

import requests
from django.views.decorators.debug import sensitive_variables

from botconfig.secrets import scrub

logger = logging.getLogger(__name__)


@dataclass
class DeliveryResult:
    ok: bool
    error: str = ""
    retry_after: float | None = None  # seconds Discord asked us to wait (rate limit)
    status_code: int | None = None
    category: str = ""   # activity.problems code when it failed
    technical: str = ""  # for debugging: HTTP status and Discord's reply, secrets removed


def _discord_reply(resp) -> str:
    try:
        return scrub((resp.text or "")[:200])
    except Exception:
        return ""


def build_payload(handle: str, display_name: str, text: str, post_url: str) -> dict:
    return {
        "embeds": [
            {
                "title": f"New post from {display_name}",
                "description": text or "(no text)",
                "url": post_url,
                "color": 0x0085FF,  # Bluesky blue
                "footer": {"text": f"@{handle} on Bluesky"},
            }
        ]
    }


@sensitive_variables("webhook_url")
def deliver_alert(webhook_url: str, handle: str, display_name: str, text: str, post_url: str) -> DeliveryResult:
    """POST a Discord embed and report exactly what happened.

    ``wait=true`` makes Discord reply only after the message is stored, so a success here
    means the message really exists. That keeps resends after a timeout rare.
    """
    payload = build_payload(handle, display_name, text, post_url)
    try:
        resp = requests.post(webhook_url, json=payload, params={"wait": "true"}, timeout=10)
        if resp.status_code == 429:
            try:
                retry_after = float(resp.json().get("retry_after", 5))
            except (ValueError, AttributeError):
                retry_after = 5.0
            logger.warning("Discord rate-limited the webhook for %s; retry in %ss", handle, retry_after)
            return DeliveryResult(False, "Discord asked the bot to slow down.", retry_after, 429,
                                  category="discord_rate_limited", technical=f"HTTP 429 retry_after={retry_after}")
        resp.raise_for_status()
        return DeliveryResult(True, status_code=resp.status_code)
    except requests.HTTPError as exc:
        code = getattr(exc.response, "status_code", None)
        # Never log `exc` itself: its text contains the webhook URL, which is a secret.
        logger.error("Discord webhook failed for %s: HTTP %s", handle, code)
        technical = f"HTTP {code}: {_discord_reply(exc.response)}" if exc.response is not None else "HTTPError"
        if code in (401, 403, 404):
            return DeliveryResult(False, "Discord didn't accept the webhook. It may have been deleted.", status_code=code,
                                  category="discord_webhook_invalid", technical=technical)
        category = "discord_unavailable" if code and code >= 500 else "discord_error"
        return DeliveryResult(False, f"Discord returned an error ({code}).", status_code=code,
                              category=category, technical=technical)
    except requests.RequestException as exc:
        logger.error("Discord webhook failed for %s: %s", handle, type(exc).__name__)
        # Only the error type: the exception text contains the webhook URL.
        return DeliveryResult(False, "Couldn't reach Discord.", category="discord_unavailable",
                              technical=type(exc).__name__)


def send_discord_alert(
    webhook_url: str,
    handle: str,
    display_name: str,
    text: str,
    post_url: str,
) -> bool:
    """POST a Discord embed to the given webhook. Returns True on success."""
    return deliver_alert(webhook_url, handle, display_name, text, post_url).ok
