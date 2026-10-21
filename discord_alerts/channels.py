"""Named Discord channels: routing, the default channel, and safe deletion.

Routing rule (step 4):
    account.channel  if the account has its own channel
    default channel  otherwise (account.channel is null)
Changing the default therefore moves only accounts that follow the default.

For rollback, two older columns are kept as copies and updated after every change here:
MonitoredAccount.discord_webhook_url (the account's own channel URL, or "") and the
saved settings' default webhook URL (BotSettings.discord_webhook_url, also copied to .env).
Step 3 code routes by exactly those, so it sends to the same places.

Webhook URLs are secrets: nothing here logs them or puts them in an error message.
"""
import logging

from django.db import transaction
from django.db.models import OuterRef, Subquery
from django.db.models.deletion import ProtectedError

from .models import DiscordWebhook

logger = logging.getLogger(__name__)

DEFAULT_NAME = "Default channel"


class ChannelError(Exception):
    """A channel change that was refused. `code` is stable for the frontend."""

    def __init__(self, code, message, status=409, **extra):
        super().__init__(message)
        self.code, self.message, self.status, self.extra = code, message, status, extra

    def as_response_body(self):
        return {"code": self.code, "message": self.message, **self.extra}


# -- reading ------------------------------------------------------------------------
def default_channel():
    from botconfig import store

    store.get()  # the very first settings import also creates the default channel
    return DiscordWebhook.objects.filter(is_default=True).first()


def route_for(account):
    """The channel this account's posts go to, or None if there is nowhere to send them."""
    if account.channel_id:
        channel = DiscordWebhook.objects.filter(pk=account.channel_id).first()
        if channel is not None:
            return channel
    return default_channel()


def default_url() -> str:
    channel = default_channel()
    return channel.url if channel else ""


# -- changes ------------------------------------------------------------------------
def unique_name(base: str, exclude_pk=None) -> str:
    name, n = base[:255], 2
    others = DiscordWebhook.objects.exclude(pk=exclude_pk)
    while others.filter(name__iexact=name).exists():
        suffix = f" ({n})"
        name, n = base[:255 - len(suffix)] + suffix, n + 1
    return name


def channel_for_url(url: str, name: str) -> DiscordWebhook:
    """Reuse the channel with exactly this URL (the oldest), or create one with `name`."""
    existing = DiscordWebhook.objects.filter(url=url).order_by("created_at", "id").first()
    if existing is not None:
        return existing
    return DiscordWebhook.objects.create(name=unique_name(name), url=url)


def make_default(channel: DiscordWebhook) -> DiscordWebhook:
    """Make this the default channel. Accounts with their own channel are not touched."""
    with transaction.atomic():
        DiscordWebhook.objects.exclude(pk=channel.pk).filter(is_default=True).update(is_default=False)
        DiscordWebhook.objects.filter(pk=channel.pk).update(is_default=True)
    channel.refresh_from_db()
    logger.info("Default channel is now %r (id %s)", channel.name, channel.pk)
    sync_rollback_copies()
    return channel


def clear_default():
    DiscordWebhook.objects.filter(is_default=True).update(is_default=False)
    logger.info("No default channel is set")
    sync_rollback_copies()


def set_default_url(url: str):
    """The settings page's single 'default webhook' field (kept from before step 4).

    A URL already used by a channel makes that channel the default; a new URL becomes a
    new channel. The previous default channel is kept (it may be assigned to accounts).
    """
    if not url:
        clear_default()
        return None
    current = default_channel()
    if current is not None and current.url == url:
        return current
    return make_default(channel_for_url(url, DEFAULT_NAME))


def assign(account, channel):
    """Give an account its own channel, or None to follow the default."""
    type(account).objects.filter(pk=account.pk).update(channel=channel)
    account.channel = channel
    sync_rollback_copies()


def delete_channel(channel: DiscordWebhook, reassign_to=None):
    """Delete a channel, refusing if that would leave accounts without their destination.

    reassign_to: None (refuse if in use), "default" (its accounts follow the default
    channel), or another DiscordWebhook (its accounts, and the default role if it has it,
    move there).
    """
    from bluesky_monitor.models import MonitoredAccount

    with transaction.atomic():
        channel = DiscordWebhook.objects.select_for_update().get(pk=channel.pk)
        if isinstance(reassign_to, DiscordWebhook) and reassign_to.pk == channel.pk:
            raise ChannelError("invalid_reassign", "Choose a different channel to move the accounts to.", 400)
        accounts = list(MonitoredAccount.objects.filter(channel=channel).order_by("handle")
                        .values("id", "handle"))

        if channel.is_default and not isinstance(reassign_to, DiscordWebhook):
            raise ChannelError(
                "channel_is_default",
                "This is the default channel. Make another channel the default first, "
                "or choose a channel to move everything to.", accounts=accounts)
        if accounts and reassign_to is None:
            raise ChannelError(
                "channel_in_use",
                f"{len(accounts)} watched account(s) still post to this channel. "
                "Choose where their posts should go instead.", accounts=accounts)

        if accounts and reassign_to == "default" and not DiscordWebhook.objects.filter(is_default=True).exists():
            raise ChannelError("no_default_channel", "There is no default channel to move these accounts to. "
                                                     "Choose a channel instead.", accounts=accounts)
        target = reassign_to if isinstance(reassign_to, DiscordWebhook) else None
        MonitoredAccount.objects.filter(channel=channel).update(channel=target)
        if channel.is_default:
            DiscordWebhook.objects.filter(pk=channel.pk).update(is_default=False)
            DiscordWebhook.objects.filter(pk=target.pk).update(is_default=True)
        try:
            DiscordWebhook.objects.filter(pk=channel.pk).delete()
        except ProtectedError:  # an account was assigned at the same moment
            raise ChannelError("channel_in_use", "A watched account was just assigned to this channel. "
                                                 "Please try again.")
    logger.info("Channel %r deleted; %d account(s) moved", channel.name, len(accounts))
    sync_rollback_copies()


# -- rollback copies ----------------------------------------------------------------
def sync_rollback_copies():
    """Copy the current routing into the pre-step-4 columns (see the module docstring)."""
    from bluesky_monitor.models import MonitoredAccount
    from botconfig import store

    MonitoredAccount.objects.filter(channel=None).exclude(discord_webhook_url="").update(discord_webhook_url="")
    MonitoredAccount.objects.exclude(channel=None).update(
        discord_webhook_url=Subquery(DiscordWebhook.objects.filter(pk=OuterRef("channel_id")).values("url")[:1]))
    default = DiscordWebhook.objects.filter(is_default=True).first()
    store.save({"discord_webhook_url": default.url if default else ""}, _from_channels=True)


def adopt_imported_default(url: str):
    """Called once, by the first settings import: the .env webhook becomes the default
    channel, unless a default was already chosen (by the migration or in the panel).
    Never clears a default."""
    if url and not DiscordWebhook.objects.filter(is_default=True).exists():
        channel = channel_for_url(url, DEFAULT_NAME)
        DiscordWebhook.objects.filter(pk=channel.pk).update(is_default=True)


def record_delivery(channel_id, ok: bool, error: str = ""):
    from django.utils import timezone

    DiscordWebhook.objects.filter(pk=channel_id).update(
        last_delivery_at=timezone.now(), last_delivery_ok=ok, last_delivery_error=(error or "")[:300])
