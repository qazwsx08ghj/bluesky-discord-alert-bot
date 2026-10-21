"""Startup check: nothing private may ever be served as a file.

Django only serves files from STATIC_ROOT, STATICFILES_DIRS and MEDIA_ROOT (and app
`static/` folders). If any of those were the project folder, or contained the database,
.env or backups/, those files could be downloaded from the control panel. This check
stops the server from starting in that case. Tests also request those paths directly.
"""
import os
from pathlib import Path

from django.conf import settings
from django.core.checks import Error, register


def private_paths():
    base = Path(settings.BASE_DIR).resolve()
    db = Path(settings.DATABASES["default"]["NAME"]).resolve()
    paths = {base / ".env", base / "backups", db, db.parent / "backups", Path(settings.BACKUP_DIR).resolve()}
    for name in ("SECRET_KEY_FILE", "LEGACY_ENV_FILE"):  # Docker: the generated key; the imported old .env
        value = os.getenv(name)
        if value:
            paths.add(Path(value).resolve())
    mirror = getattr(settings, "SETTINGS_ENV_MIRROR_PATH", "")
    if mirror:
        paths.add(Path(mirror).resolve())
    return paths


def served_dirs():
    dirs = []
    for value in (getattr(settings, "STATIC_ROOT", None), getattr(settings, "MEDIA_ROOT", None)):
        if value:
            dirs.append(value)
    for entry in getattr(settings, "STATICFILES_DIRS", []):
        dirs.append(entry[1] if isinstance(entry, (list, tuple)) else entry)
    return [Path(d).resolve() for d in dirs]


@register("security")
def check_private_files_not_served(app_configs=None, **kwargs):
    errors = []
    for served in served_dirs():
        for private in private_paths():
            if private == served or served in private.parents:
                errors.append(Error(
                    f"The folder {served} is served to the browser and contains private files "
                    f"({private.name}). Serve static files from a folder of their own.",
                    id="botconfig.E001"))
    return errors
