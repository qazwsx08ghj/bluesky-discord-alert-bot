"""Named Discord channels: routing, the default channel, and safe deletion.

Routing rule: an account sends to each of its destinations (AccountDestination):
    destination.channel  if the destination names a channel
    default channel      if it follows the default (destination.channel is null)
Each channel gets a post once: if the default is also one of the account's own channels,
the account's own destination (and its role) is used. Changing the default therefore moves
only destinations that follow the default.

What a post is sent to is fixed when the post is noticed (one PostDelivery per channel), so
changing channels, roles or the default later never sends old posts again.

Webhook URLs are secrets: nothing here logs them or puts them in an error message.
"""
import logging
from dataclasses import dataclass

from django.db import IntegrityError, transaction
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


@dataclass(frozen=True)
class Target:
    """One channel a post goes to, and the role to mention there ("" = none)."""
    channel: DiscordWebhook
    role_id: str = ""


# -- reading ------------------------------------------------------------------------
def default_channel():
    from botconfig import store

    store.get()  # the very first settings import also creates the default channel
    return DiscordWebhook.objects.filter(is_default=True).first()


_LOOK_UP = object()


def targets_for(account, destinations=None, default=_LOOK_UP) -> list:
    """Where this account's posts go now: one Target per channel, in the order they were added.

    Empty if there is nowhere to send them (no destinations, or only "the default channel"
    and no default is set). `destinations` and the `default` channel (or None) may be passed
    when they are already loaded.
    """
    from bluesky_monitor.models import AccountDestination

    if destinations is None:
        destinations = AccountDestination.objects.filter(account=account).select_related("channel")
    destinations = sorted(destinations, key=lambda d: d.pk or 0)
    own = {d.channel_id for d in destinations if d.channel_id is not None}
    if not any(d.channel_id is None for d in destinations):
        default = None
    elif default is _LOOK_UP:
        default = default_channel()
    targets = []
    for d in destinations:
        if d.channel_id is not None:
            targets.append(Target(d.channel, d.role_id))
        elif default is not None and default.pk not in own:
            targets.append(Target(default, d.role_id))
    return targets


def route_for(account):
    """The first channel this account's posts go to, or None. Meant for accounts with one destination."""
    targets = targets_for(account)
    return targets[0].channel if targets else None


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
    """Make this the default channel. Accounts' own channels are not touched; posts already
    noticed keep going where they were going."""
    with transaction.atomic():
        DiscordWebhook.objects.exclude(pk=channel.pk).filter(is_default=True).update(is_default=False)
        DiscordWebhook.objects.filter(pk=channel.pk).update(is_default=True)
    channel.refresh_from_db()
    logger.info("Default channel is now %r (id %s)", channel.name, channel.pk)
    return channel


def clear_default():
    DiscordWebhook.objects.filter(is_default=True).update(is_default=False)
    logger.info("No default channel is set")


def set_default_url(url: str):
    """Make the channel with this URL the default (`import_env_settings --force`).

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
    """Make `channel` (None = the default channel) the account's only destination, without a role."""
    set_destinations(account, [{"channel": channel, "role_id": ""}])


def set_destinations(account, items):
    """Replace an account's destinations with `items`: [{"channel": channel or None, "role_id": str}].

    The items must already be checked (see the account serializer); they are kept in the
    order given. Posts already noticed keep the deliveries they have (with the role set at
    the time), except that unsent ones (pending or waiting to retry) to a channel the account
    no longer sends to are cancelled. A send already in progress can't be recalled; it finishes.
    """
    from bluesky_monitor.models import AccountDestination

    with transaction.atomic():
        before = {t.channel.pk for t in targets_for(account)}
        AccountDestination.objects.filter(account=account).delete()  # nothing refers to these rows
        AccountDestination.objects.bulk_create(
            AccountDestination(account=account, channel=item.get("channel"), role_id=item.get("role_id") or "")
            for item in items)
        after = {t.channel.pk for t in targets_for(account)}
        cancel_unsent(channel_ids=before - after, account=account)


def cancel_unsent(*, channel_ids, account=None) -> int:
    """Cancel unsent deliveries (pending or waiting to retry) to these channels. Returns how many.

    A delivery being sent right now is left alone: the request may already have reached
    Discord, and its result is recorded when it finishes.
    """
    from bluesky_monitor import poller
    from bluesky_monitor.models import PostDelivery

    if not channel_ids:
        return 0
    rows = PostDelivery.objects.filter(channel_id__in=channel_ids, status__in=PostDelivery.CANCELLABLE)
    if account is not None:
        rows = rows.filter(post__account=account)
    found = list(rows.values_list("pk", "post_id"))
    count = PostDelivery.objects.filter(pk__in=[pk for pk, _ in found], status__in=PostDelivery.CANCELLABLE).update(
        status=PostDelivery.CANCELLED, next_attempt_at=None)
    for post_id in {post_id for _, post_id in found}:
        poller.refresh_post_status(post_id)
    if count:
        logger.info("Cancelled %d unsent notification(s)", count)
    return count


def _move_unsent(channel, target):
    """Move unsent deliveries from `channel` to `target` (a channel, or None to cancel them).

    Never makes a duplicate: a post that already has a delivery to `target` (in any state)
    keeps that one, and the moved one is cancelled. Only rows that are still unsent at that
    moment are touched, and a send claims a row only if its channel hasn't changed
    (poller.deliver_pending), so a delivery can't be sent to one channel and recorded for another.
    """
    from bluesky_monitor import poller
    from bluesky_monitor.models import PostDelivery

    for row in list(PostDelivery.objects.filter(channel=channel, status__in=PostDelivery.CANCELLABLE)):
        moved = 0
        if target is not None and not PostDelivery.objects.filter(post_id=row.post_id, channel=target).exists():
            try:
                with transaction.atomic():
                    moved = PostDelivery.objects.filter(pk=row.pk, channel=channel,
                                                        status__in=PostDelivery.CANCELLABLE).update(channel=target)
            except IntegrityError:  # a delivery of this post to the target appeared meanwhile
                moved = 0
        if not moved:
            PostDelivery.objects.filter(pk=row.pk, status__in=PostDelivery.CANCELLABLE).update(
                status=PostDelivery.CANCELLED, next_attempt_at=None)
        poller.refresh_post_status(row.post_id)


def delete_channel(channel: DiscordWebhook, reassign_to=None):
    """Delete a channel, refusing if that would leave accounts without their destination.

    reassign_to: None (refuse if in use), "default" (its accounts follow the default
    channel), or another DiscordWebhook (its accounts, and the default role if it has it,
    move there). An account that already sends to the new place keeps that destination and
    its role. Unsent notifications move along, unless the post already goes there; then they
    are cancelled, so nothing is sent twice. A send in progress can't be recalled.
    """
    from bluesky_monitor.models import AccountDestination, MonitoredAccount, PostDelivery

    with transaction.atomic():
        channel = DiscordWebhook.objects.select_for_update().get(pk=channel.pk)
        if isinstance(reassign_to, DiscordWebhook) and reassign_to.pk == channel.pk:
            raise ChannelError("invalid_reassign", "Choose a different channel to move the accounts to.", 400)
        accounts = list(MonitoredAccount.objects.filter(destinations__channel=channel).distinct()
                        .order_by("handle").values("id", "handle"))

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

        default = DiscordWebhook.objects.filter(is_default=True).first()
        if accounts and reassign_to == "default" and default is None:
            raise ChannelError("no_default_channel", "There is no default channel to move these accounts to. "
                                                     "Choose a channel instead.", accounts=accounts)
        target = reassign_to if isinstance(reassign_to, DiscordWebhook) else None
        for row in list(AccountDestination.objects.filter(channel=channel)):
            if AccountDestination.objects.filter(account_id=row.account_id, channel=target).exists():
                row.delete()  # the account already sends there; that destination and its role stay
            else:
                AccountDestination.objects.filter(pk=row.pk).update(channel=target)
        if channel.is_default:
            DiscordWebhook.objects.filter(pk=channel.pk).update(is_default=False)
            DiscordWebhook.objects.filter(pk=target.pk).update(is_default=True)
        _move_unsent(channel, target if target is not None else (default if reassign_to == "default" else None))
        # The channel's remaining deliveries (sent, given up, or being sent right now) go with it;
        # their posts' statuses are summed up again afterwards.
        affected = list(PostDelivery.objects.filter(channel=channel).values_list("post_id", flat=True).distinct())
        try:
            DiscordWebhook.objects.filter(pk=channel.pk).delete()
        except ProtectedError:  # an account was given this channel at the same moment
            raise ChannelError("channel_in_use", "A watched account was just assigned to this channel. "
                                                 "Please try again.")
        from bluesky_monitor import poller
        from bluesky_monitor.models import SeenPost
        for post_id in affected:
            poller.refresh_post_status(post_id)
        # A post that was only waiting for this channel has nowhere left to go.
        SeenPost.objects.filter(pk__in=affected, status=SeenPost.PENDING, deliveries__isnull=True).update(
            status=SeenPost.SKIPPED)
    logger.info("Channel %r deleted; %d account(s) moved", channel.name, len(accounts))


def adopt_imported_default(url: str):
    """Called once, by the first settings import: an earlier install's .env webhook becomes
    the default channel, unless a default was already chosen (by a migration or in the
    panel). Never clears a default."""
    if url and not DiscordWebhook.objects.filter(is_default=True).exists():
        channel = channel_for_url(url, DEFAULT_NAME)
        DiscordWebhook.objects.filter(pk=channel.pk).update(is_default=True)


def record_delivery(channel_id, ok: bool, error: str = ""):
    from django.utils import timezone

    DiscordWebhook.objects.filter(pk=channel_id).update(
        last_delivery_at=timezone.now(), last_delivery_ok=ok, last_delivery_error=(error or "")[:300])


def remember_guild(channel_id, guild_id: str):
    DiscordWebhook.objects.filter(pk=channel_id).update(guild_id=guild_id or "")
