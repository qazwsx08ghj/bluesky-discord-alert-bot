"""A logging filter that masks Discord webhook URLs, as a safety net.

The bot's own messages never include webhook URLs, but third-party libraries (requests,
urllib3, Django) may put a URL in an exception or debug message. This filter is attached
to every console handler in settings.LOGGING.
"""
import logging

from botconfig.secrets import scrub


def redact(text: str) -> str:
    """Mask webhook tokens, session tokens and password-like values (see botconfig.secrets.scrub)."""
    return scrub(text)


class RedactWebhooks(logging.Filter):
    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:
            return True
        clean = redact(message)
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if clean != message:
            record.msg, record.args = clean, None
        return True
