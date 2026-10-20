import logging
import requests

logger = logging.getLogger(__name__)


def send_discord_alert(
    webhook_url: str,
    handle: str,
    display_name: str,
    text: str,
    post_url: str,
) -> bool:
    """POST a Discord embed to the given webhook. Returns True on success."""
    payload = {
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

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.error("Discord webhook failed for %s: %s", handle, exc)
        return False
