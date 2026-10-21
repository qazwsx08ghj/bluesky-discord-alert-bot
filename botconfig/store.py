"""The bot's settings: read from and saved to the database.

Rules (details and the rollback procedure: docs/SETTINGS_MIGRATION.md):

1. The database row is the source of truth. Every reader asks this module each time, so
   a saved change applies without a restart.
2. The first time settings are needed and no row exists, they are imported once from the
   environment (the .env values Django loaded at start). A row is never overwritten
   from .env afterwards; `manage.py import_env_settings --force` is the explicit way.
3. Saving: validate first, then write the database (one conditional UPDATE), then copy the
   result into .env for rollback. A failed .env copy never undoes or blocks the database
   save: it is recorded on the row, reported to the caller, and repaired automatically
   on the next save or checker start, because .env carries the settings version it was
   written from (BOT_SETTINGS_VERSION).
4. Secret values (password, webhook URL) are never logged or returned. Logs name keys only.
"""
import logging
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from . import envfile
from .models import MAX_INTERVAL, MIN_INTERVAL, BotSettings

logger = logging.getLogger(__name__)

# model field -> .env key
ENV_KEYS = {
    "bluesky_handle": "BLUESKY_HANDLE",
    "bluesky_password": "BLUESKY_PASSWORD",
    "discord_webhook_url": "DISCORD_WEBHOOK_URL",
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
    env_file_ok: bool = True
    env_file_error: str = ""


# -- reading ----------------------------------------------------------------------
def get() -> BotSettings:
    """The current settings, importing them from the environment the very first time."""
    row = BotSettings.objects.filter(pk=1).first()
    return row if row is not None else _import_once()


def credentials() -> tuple:
    row = get()
    return row.bluesky_handle, row.bluesky_password


def default_webhook_url() -> str:
    """The default channel's URL (step 4: channels are the source; the row holds a copy)."""
    from discord_alerts import channels

    return channels.default_url()


def poll_interval_minutes() -> int:
    return get().poll_interval_minutes


# -- the one-time import -----------------------------------------------------------
@sensitive_variables("values")
def _values_from_environment() -> dict:
    """The legacy values Django loaded from .env / the process environment at start."""
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
    return ", ".join(f"{ENV_KEYS[f]}={'set' if values.get(f) else 'empty'}" for f in FIELDS)


@sensitive_variables("values")
def _import_once() -> BotSettings:
    values = _values_from_environment()
    found = any(values[f] for f in ("bluesky_handle", "bluesky_password", "discord_webhook_url"))
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

    channels.adopt_imported_default(values["discord_webhook_url"])
    if found:
        logger.info("Settings imported from .env into the database (one time): %s", _describe(values))
    else:
        logger.info("No settings found in .env; starting with empty settings")
    return row


@sensitive_variables("values")
def import_from_environment(force: bool = False) -> tuple:
    """Explicit import (management command). Returns (row, imported: bool)."""
    if not force:
        existing = BotSettings.objects.filter(pk=1).first()
        if existing is not None:
            return existing, False
        return get(), True
    values = _values_from_environment()
    result = save(values, keep_blank_password=True)
    BotSettings.objects.filter(pk=1).update(source=BotSettings.SOURCE_ENV, imported_at=timezone.now())
    return result.settings, True


# -- saving -----------------------------------------------------------------------
@sensitive_variables("changes", "values")
def save(changes: dict, keep_blank_password: bool = True, attempts: int = 5, _from_channels=False) -> SaveResult:
    """Save already-validated changes. Database first; then the .env copy.

    The default webhook is a channel since step 4: a new URL here goes through
    discord_alerts.channels, which then copies it back into this row for rollback.
    Raises SettingsSaveError if the database can't be written (nothing changed then).
    """
    values = {k: v for k, v in changes.items() if k in FIELDS}
    if keep_blank_password and not values.get("bluesky_password"):
        values.pop("bluesky_password", None)
    if "discord_webhook_url" in values and not _from_channels:
        from discord_alerts import channels

        try:
            get()
            channels.set_default_url(values.pop("discord_webhook_url"))
        except DatabaseError as exc:
            raise SettingsSaveError("The settings couldn't be saved to the database. Nothing was changed.") from exc

    try:
        get()  # make sure the row exists (first-time import)
        for _ in range(attempts):
            current = BotSettings.objects.get(pk=1)
            changed = [f for f, v in values.items() if getattr(current, f) != v]
            if not changed:  # (a default webhook change was already saved via channels)
                result = SaveResult(current, [], env_file_ok=current.env_mirror_ok,
                                    env_file_error=current.env_mirror_error)
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
        ok, error = sync_env_file()
        result.env_file_ok, result.env_file_error = ok, error
        result.settings.refresh_from_db()
    return result


# -- the .env copy kept for rollback -------------------------------------------------
def env_path():
    path = getattr(settings, "SETTINGS_ENV_MIRROR_PATH", "")
    return Path(path) if path else None


def _env_values(row: BotSettings) -> dict:
    values = {ENV_KEYS[f]: str(getattr(row, f)) for f in FIELDS}
    values[envfile.STAMP_KEY] = str(row.version)
    return values


@sensitive_variables("values")
def sync_env_file(force: bool = False) -> tuple:
    """Bring .env up to the database's settings if it is behind. Returns (ok, error)."""
    path = env_path()
    if path is None:
        return True, ""
    for _ in range(3):
        row = get()
        on_disk = envfile.read(path)
        if not force and on_disk.get(envfile.STAMP_KEY) == str(row.version):
            _record_mirror(row.version, True, "")
            return True, ""
        values = _env_values(row)
        try:
            _backup_original_env(path, on_disk)
            envfile.write(path, values)
        except OSError as exc:
            error = f"The .env file couldn't be updated ({exc.__class__.__name__}: {exc.strerror or 'error'})."
            logger.warning("%s The database has the current settings; .env will be updated on the next "
                           "save or start.", error)
            _record_mirror(row.version, False, error)
            return False, error
        # If another save landed while we wrote, write again so .env ends at the newest version.
        if BotSettings.objects.filter(pk=1, version=row.version).exists():
            _record_mirror(row.version, True, "")
            return True, ""
    return True, ""


def _record_mirror(version: int, ok: bool, error: str):
    BotSettings.objects.filter(pk=1, version=version).update(
        env_mirror_ok=ok, env_mirror_error=error[:300], env_mirror_at=timezone.now())


def _backup_original_env(path: Path, on_disk: dict):
    """Before the first managed write, keep a copy of .env as the user wrote it."""
    if envfile.STAMP_KEY in on_disk or not path.exists():
        return
    copy = path.with_name(".env.before-db-settings")
    if not copy.exists():
        copy.write_bytes(path.read_bytes())


def env_file_differences() -> list:
    """Keys whose .env value differs from the database although .env is at the current version.

    That means .env was edited by hand (or by older code after a rollback). The database
    still wins; the caller warns, and `import_env_settings --force` applies the .env values.
    """
    path = env_path()
    if path is None or not path.exists():
        return []
    row = get()
    on_disk = envfile.read(path)
    if on_disk.get(envfile.STAMP_KEY) != str(row.version):
        return []
    current = _env_values(row)
    return [key for key in ENV_KEYS.values() if key in on_disk and on_disk[key] != current[key]]


def startup_check():
    """Run when the checker starts: import if needed, repair a stale .env, warn about edits."""
    get()
    ok, _ = sync_env_file()
    differing = env_file_differences() if ok else []
    if differing:
        logger.warning(
            "%s in .env differ from the saved settings and are ignored (the database is used). "
            "Change settings in the control panel, or run `python manage.py import_env_settings --force` "
            "to load the .env values.", ", ".join(differing))
    return differing
