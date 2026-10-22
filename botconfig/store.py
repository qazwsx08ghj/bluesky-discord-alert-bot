"""The bot's settings: read from and saved to the database.

Rules:

1. The database row is the only place the settings are kept. Every reader asks this
   module each time, so a saved change applies without a restart.
2. The first time settings are needed and no row exists, they are imported once from an
   earlier install's .env (setup.bat points LEGACY_ENV_FILE at it; without Docker, the
   values Django loaded at start). The .env webhook becomes the default Discord channel.
   A row is never overwritten from .env afterwards; `manage.py import_env_settings --force`
   is the explicit way.
3. Saving: validate first, then one conditional UPDATE, so two saves at once can't lose
   either change.
4. Secret values (password, webhook URL) are never logged or returned. Logs name keys only.

Older versions also copied the saved settings back into .env, and the default webhook
URL into this row, so older versions could run after a rollback. Both copies are gone; a
rollback now means restoring a backup (docs/DOCKER.md).
"""
import logging
from dataclasses import dataclass

from django.conf import settings
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from .models import MAX_INTERVAL, MIN_INTERVAL, BotSettings

logger = logging.getLogger(__name__)

# model field -> the .env key it was imported from
ENV_KEYS = {
    "bluesky_handle": "BLUESKY_HANDLE",
    "bluesky_password": "BLUESKY_PASSWORD",
    "poll_interval_minutes": "POLL_INTERVAL_MINUTES",
}
FIELDS = tuple(ENV_KEYS)
DEFAULT_INTERVAL = 5


class SettingsSaveError(Exception):
    """The database could not be updated. Nothing was changed."""


@dataclass
class SaveResult:
    settings: BotSettings
    changed: list


# -- reading ----------------------------------------------------------------------
def get() -> BotSettings:
    """The current settings, importing them from an earlier install the very first time."""
    row = BotSettings.objects.filter(pk=1).first()
    return row if row is not None else _import_once()


def credentials() -> tuple:
    row = get()
    return row.bluesky_handle, row.bluesky_password


def default_webhook_url() -> str:
    """The default channel's URL, or ""."""
    from discord_alerts import channels

    return channels.default_url()


def poll_interval_minutes() -> int:
    return get().poll_interval_minutes


# -- the one-time import -----------------------------------------------------------
@sensitive_variables("values")
def _values_from_environment() -> dict:
    """The values of an earlier install (its .env), as Django loaded them at start."""
    values = {
        "bluesky_handle": str(getattr(settings, "BLUESKY_HANDLE", "") or "").strip().lstrip("@"),
        "bluesky_password": str(getattr(settings, "BLUESKY_PASSWORD", "") or "").strip(),
        "discord_webhook_url": str(getattr(settings, "DISCORD_WEBHOOK_URL", "") or "").strip(),
    }
    raw = getattr(settings, "POLL_INTERVAL_MINUTES", DEFAULT_INTERVAL)
    try:
        interval = int(raw)
    except (TypeError, ValueError):
        logger.warning("POLL_INTERVAL_MINUTES in .env isn't a number; using %d", DEFAULT_INTERVAL)
        interval = DEFAULT_INTERVAL
    if not MIN_INTERVAL <= interval <= MAX_INTERVAL:
        clamped = min(max(interval, MIN_INTERVAL), MAX_INTERVAL)
        logger.warning("POLL_INTERVAL_MINUTES in .env is outside %d-%d; using %d",
                       MIN_INTERVAL, MAX_INTERVAL, clamped)
        interval = clamped
    values["poll_interval_minutes"] = interval
    return values


def _describe(values: dict) -> str:
    """Which settings were found, without their values."""
    keys = {**ENV_KEYS, "discord_webhook_url": "DISCORD_WEBHOOK_URL"}
    return ", ".join(f"{keys[f]}={'set' if values.get(f) else 'empty'}" for f in keys)


@sensitive_variables("values")
def _import_once() -> BotSettings:
    values = _values_from_environment()
    webhook = values.pop("discord_webhook_url")
    found = bool(webhook or values["bluesky_handle"] or values["bluesky_password"])
    try:
        with transaction.atomic():
            row = BotSettings.objects.create(
                pk=1, **values,
                source=BotSettings.SOURCE_ENV if found else BotSettings.SOURCE_DEFAULTS,
                imported_at=timezone.now() if found else None,
            )
    except IntegrityError:  # another process imported at the same moment; use its row
        return BotSettings.objects.get(pk=1)
    from discord_alerts import channels

    channels.adopt_imported_default(webhook)
    if found:
        logger.info("Settings imported from .env into the database (one time): %s",
                    _describe({**values, "discord_webhook_url": webhook}))
    else:
        logger.info("No settings found in .env; starting with empty settings")
    return row


@sensitive_variables("values", "webhook")
def import_from_environment(force: bool = False) -> tuple:
    """Explicit import (management command). Returns (row, imported: bool)."""
    if not force:
        existing = BotSettings.objects.filter(pk=1).first()
        if existing is not None:
            return existing, False
        return get(), True
    values = _values_from_environment()
    webhook = values.pop("discord_webhook_url")
    save(values, keep_blank_password=True)
    if webhook:
        from discord_alerts import channels

        channels.set_default_url(webhook)
    BotSettings.objects.filter(pk=1).update(source=BotSettings.SOURCE_ENV, imported_at=timezone.now())
    return BotSettings.objects.get(pk=1), True


# -- saving -----------------------------------------------------------------------
@sensitive_variables("changes", "values")
def save(changes: dict, keep_blank_password: bool = True, attempts: int = 5) -> SaveResult:
    """Save already-validated changes. Raises SettingsSaveError if the database can't be
    written (nothing changed then)."""
    values = {k: v for k, v in changes.items() if k in FIELDS}
    if keep_blank_password and not values.get("bluesky_password"):
        values.pop("bluesky_password", None)
    try:
        get()  # make sure the row exists (first-time import)
        for _ in range(attempts):
            current = BotSettings.objects.get(pk=1)
            changed = [f for f, v in values.items() if getattr(current, f) != v]
            if not changed:
                result = SaveResult(current, [])
                break
            # Conditional on the version we read, so two saves at once can't lose either change.
            updated = BotSettings.objects.filter(pk=1, version=current.version).update(
                **{f: values[f] for f in changed}, version=current.version + 1, updated_at=timezone.now())
            if updated:
                result = SaveResult(BotSettings.objects.get(pk=1), changed)
                break
        else:
            raise SettingsSaveError("The settings were being changed elsewhere; please try again.")
    except DatabaseError as exc:
        logger.error("Saving settings to the database failed (%s); nothing was changed", type(exc).__name__)
        raise SettingsSaveError("The settings couldn't be saved to the database. Nothing was changed.") from exc

    if result.changed:
        logger.info("Settings saved (version %d): %s changed", result.settings.version,
                    ", ".join(ENV_KEYS[f] for f in result.changed))
    return result


def startup_check():
    """Run when the checker starts: import the settings once if there are none yet."""
    return get()
