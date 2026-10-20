"""Locks shared by every process through the database.

Each lock is taken with a single conditional UPDATE. SQLite (and any other database)
runs that statement atomically, so two processes can never both succeed. Locks carry
an expiry time: a lock that is not renewed in time is stale and can be taken over,
which is how a crashed process is recovered from.
"""
import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from .models import MonitoredAccount, SchedulerState

logger = logging.getLogger(__name__)


def _state_row():
    """Make sure the single SchedulerState row exists (safe if two processes race)."""
    if not SchedulerState.objects.filter(pk=1).exists():
        try:
            with transaction.atomic():
                SchedulerState.objects.create(pk=1)
        except IntegrityError:
            pass  # another process created it first


def acquire_scheduler_lease(instance_id: str, seconds: int | None = None) -> bool:
    """Take or renew the checker lease. Returns True when this instance holds it.

    Succeeds when nobody holds the lease, this instance already holds it, or the
    holder's lease has expired (it crashed or was killed without releasing).
    """
    seconds = seconds or settings.SCHEDULER_LEASE_SECONDS
    _state_row()
    now = timezone.now()
    free_or_mine = (
        Q(instance_id="") | Q(instance_id=instance_id)
        | Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lt=now)
    )
    taken = SchedulerState.objects.filter(pk=1).filter(free_or_mine).update(
        instance_id=instance_id,
        lease_expires_at=now + timedelta(seconds=seconds),
        heartbeat_at=now,
    )
    return taken == 1


def holds_scheduler_lease(instance_id: str) -> bool:
    return SchedulerState.objects.filter(
        pk=1, instance_id=instance_id, lease_expires_at__gt=timezone.now()
    ).exists()


def release_scheduler_lease(instance_id: str) -> None:
    """Give the lease up straight away (on clean shutdown), so a new checker can start at once."""
    SchedulerState.objects.filter(pk=1, instance_id=instance_id).update(
        instance_id="", lease_expires_at=None
    )


def acquire_account_lock(account_id: int, seconds: int | None = None) -> str | None:
    """Lock one account for a check. Returns a token, or None if another check holds it."""
    seconds = seconds or settings.ACCOUNT_LOCK_SECONDS
    token = uuid.uuid4().hex
    now = timezone.now()
    taken = MonitoredAccount.objects.filter(pk=account_id).filter(
        Q(check_lock_until__isnull=True) | Q(check_lock_until__lt=now)
    ).update(check_lock_token=token, check_lock_until=now + timedelta(seconds=seconds))
    return token if taken == 1 else None


def release_account_lock(account_id: int, token: str) -> None:
    """Release only if we still own the lock (it may have expired and been taken over)."""
    MonitoredAccount.objects.filter(pk=account_id, check_lock_token=token).update(
        check_lock_token="", check_lock_until=None
    )
