"""Showing secrets safely. A Discord webhook URL works like a password: anyone who has it
can post into the channel, so API responses show only a masked form."""
import re

MASK = "****"
_WEBHOOK = re.compile(r"^(https?://[^/]+/api(?:/v\d+)?/webhooks/\d+/)([^/?#]+)")


def mask_webhook_url(url: str) -> str:
    """`https://discord.com/api/webhooks/123/abcdef...wxyz` -> `.../webhooks/123/****wxyz`."""
    if not url:
        return ""
    match = _WEBHOOK.match(url)
    if match:
        token = match.group(2)
        return f"{match.group(1)}{MASK}{token[-4:] if len(token) > 12 else ''}"
    host = re.match(r"^(https?://[^/]+)", url)
    return f"{host.group(1) if host else ''}/{MASK}"


def is_masked(value: str) -> bool:
    return MASK in (value or "")
