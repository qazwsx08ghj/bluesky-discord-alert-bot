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


# -- removing secrets from free text (logs, activity records, error details) ---------------
_SECRET_PATTERNS = [
    # Discord webhook token: .../api/webhooks/<id>/<token>
    (re.compile(r"(/api(?:/v\d+)?/webhooks/\d+/)[A-Za-z0-9_\-]+"), r"\1" + MASK),
    # Bluesky session tokens are JWTs.
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*"), MASK),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{6,}"), r"\1 " + MASK),
    # key=value / "key": "value" for anything that names a secret.
    (re.compile(r"""(?ix)
        ( ["']?(?:app_?password|password|passwd|access_?jwt|refresh_?jwt|access_?token|refresh_?token
               |session_?string|secret_?key|api_?key|authorization|token)["']?\s*[:=]\s* )
        (?!(?:set|empty|unset|changed|none|null|true|false)\b|\*{4})   # status words, already masked
        ( "[^"]*" | '[^']*' | [^\s,;&}\]]+ )"""), r"\1" + MASK),
    # Bluesky App Passwords look like abcd-efgh-ijkl-mnop.
    (re.compile(r"\b[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}\b"), "****-****-****-****"),
]


def scrub(text, known_secrets=()) -> str:
    """Remove secrets from free text: webhook tokens, session tokens, passwords, and any
    exact value in known_secrets (e.g. the saved App Password)."""
    if not text:
        return "" if text is None else str(text)
    text = str(text)
    for secret in known_secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, MASK)
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text
