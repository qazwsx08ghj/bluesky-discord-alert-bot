"""A logging filter that masks Discord webhook URLs, as a safety net.

The bot's own messages never include webhook URLs, but third-party libraries (requests,
urllib3, Django) may put a URL in an exception or debug message. This filter is attached
to every console handler in settings.LOGGING.
"""
import logging
import re

_WEBHOOK = re.compile(r"(/api(?:/v\d+)?/webhooks/\d+/)[A-Za-z0-9_\-]+")


def redact(text: str) -> str:
    return _WEBHOOK.sub(r"\1****", text)


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
