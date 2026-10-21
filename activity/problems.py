"""Problem categories: what went wrong, said plainly, and what to do about it.

Every failure the bot records gets one category code. The code goes into activity events,
the status endpoint and the API; the wording below is what a non-technical user sees.
The technical cause (exception type, HTTP status) is stored separately, for debugging.

`fix` tells the control panel which button or page to offer:
  update_login    open Settings at the Bluesky login
  update_webhook  open the channel to paste a new webhook URL
  choose_channel  open Channels to pick a default, or assign one to the account
  check_handle    open the account to correct or remove it
  restart_bot     show how to restart the bot
  wait            nothing to do; the bot retries by itself
  none            nothing the user can do; details are kept for troubleshooting
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    code: str
    title: str
    explanation: str
    fix: str

    def as_dict(self):
        return {"category": self.code, "title": self.title, "explanation": self.explanation, "fix": self.fix}


RESTART_HELP = ("restart the bot: run start.bat again (or restart the bot in Docker Desktop). "
                "Your settings, accounts and unsent posts are kept.")

_ALL = [
    # Bluesky
    Category("bluesky_not_configured", "Bluesky login isn't set up",
             "Add the bot's Bluesky handle and App Password in Settings. Until then nothing is checked.",
             "update_login"),
    Category("bluesky_login_rejected", "Bluesky didn't accept the login",
             "Check the bot's Bluesky handle and App Password in Settings. An App Password looks like "
             "xxxx-xxxx-xxxx-xxxx and is created in Bluesky under Settings > Privacy and security > App passwords. "
             "Checks are paused until the login works.",
             "update_login"),
    Category("bluesky_rate_limited", "Bluesky asked the bot to slow down",
             "The bot waits and tries again by itself. If this keeps happening, choose a longer check interval "
             "in Settings.",
             "wait"),
    Category("bluesky_unavailable", "Bluesky couldn't be reached",
             "This is usually temporary; the bot tries again at the next check. If it lasts more than an hour, "
             "check that this computer is connected to the internet.",
             "wait"),
    Category("account_not_found", "Account not found on Bluesky",
             "Bluesky couldn't find this account. Check that the handle is spelled correctly "
             "(for example name.bsky.social), or remove the account if it was deleted.",
             "check_handle"),
    Category("bluesky_error", "Bluesky returned an error",
             "Bluesky answered with an unexpected error. The bot tries again at the next check.",
             "wait"),
    # Discord
    Category("discord_webhook_invalid", "Discord channel link no longer works",
             "Discord didn't accept the webhook; it may have been deleted in Discord. In Discord, open the "
             "channel's settings > Integrations > Webhooks, copy a webhook URL, and paste it into this channel. "
             "Posts that couldn't be sent are retried for up to 24 hours.",
             "update_webhook"),
    Category("discord_rate_limited", "Discord asked the bot to slow down",
             "The bot waits as long as Discord asks and then sends the post. Nothing is lost.",
             "wait"),
    Category("discord_unavailable", "Discord couldn't be reached",
             "This is usually temporary; the bot retries the post automatically for up to 24 hours. If it lasts, "
             "check that this computer is connected to the internet.",
             "wait"),
    Category("discord_error", "Discord refused the message",
             "The bot retries the post automatically. If it keeps failing, use Test on the channel to check it.",
             "update_webhook"),
    Category("discord_test_failed", "The test message wasn't delivered",
             "Discord didn't accept the test message. Check that the webhook still exists in Discord, "
             "or paste a new webhook URL into this channel.",
             "update_webhook"),
    Category("send_expired", "A post was never sent",
             "The bot couldn't send this post within 24 hours (for example because Discord kept failing, or the "
             "bot was turned off). It was dropped so old posts don't arrive late.",
             "none"),
    Category("no_channel", "No Discord channel chosen",
             "New posts from this account aren't sent anywhere. Choose a default channel, or give the account "
             "its own channel.",
             "choose_channel"),
    # The checker itself
    Category("scheduler_not_started", "The checker hasn't started",
             "If you just started the bot, wait a minute. Otherwise, " + RESTART_HELP,
             "restart_bot"),
    Category("scheduler_stopped", "The checker stopped",
             "The control panel still works, but no new posts are being checked or sent. To fix it, "
             + RESTART_HELP,
             "restart_bot"),
    Category("scheduler_stalled", "Checks are overdue",
             "The checker is running but a check is late or stuck. It usually recovers by itself; "
             "if this lasts more than 15 minutes, " + RESTART_HELP,
             "restart_bot"),
    Category("internal", "Something went wrong inside the bot",
             "The bot keeps running and tries again at the next check. If this keeps happening, the technical "
             "details help with troubleshooting.",
             "none"),
]

CATEGORIES = {c.code: c for c in _ALL}


def get(code):
    return CATEGORIES.get(code or "")


def describe(code):
    """{category, title, explanation, fix} for a code, or None."""
    category = get(code)
    return category.as_dict() if category else None


def technical(exc: BaseException) -> str:
    """A short technical description of an exception for debugging (secrets removed later)."""
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def classify_bluesky_error(exc: BaseException) -> str:
    """The category for an error raised while reading an account's feed."""
    code = getattr(exc, "category", None)
    if code in CATEGORIES:
        return code
    from atproto_client import exceptions as at_exc

    if isinstance(exc, at_exc.RateLimitExceededError):
        return "bluesky_rate_limited"
    if isinstance(exc, (at_exc.NetworkError, at_exc.RequestException)):
        return "bluesky_unavailable"
    if isinstance(exc, at_exc.BadRequestError):
        # Unknown or malformed handles: {"error": "InvalidRequest", "message": "Profile not found"}
        return "account_not_found"
    if isinstance(exc, at_exc.AtProtocolError):
        return "bluesky_error"
    return "internal"
