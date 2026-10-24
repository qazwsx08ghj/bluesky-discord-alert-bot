"""The deployment files inside the image: /app/bundle/<platform>/ with bundle.json.

    /app/bundle/windows/
        bundle.json                  {"schema": "alertbot-bundle/1", "platform": "windows",
                                      "files": {"update.bat": "<sha256>", ...}}
        setup.bat start.bat update.bat stop.bat backup.bat restore.bat
        docker-compose.yml  docker/scripts/common.bat
        README.txt  README.zh-TW.txt  LICENSE  THIRD_PARTY_NOTICES.txt

Written when the image is built (docker/make_release_bundle.py --export), owned by root, so
the bot can read but not change them. An updater copies the folder out with `docker cp` and
checks it with verify() (M3). Never in a bundle: docker/compose.env (a release's own image pin)
and anything of a single install (its port, data, backups, logs, .env).
"""
import hashlib
import json
from pathlib import Path

from . import MetadataError
from .manifest import SHA256, WINDOWS_FILES

SCHEMA = "alertbot-bundle/1"
INDEX = "bundle.json"
NEVER_BUNDLED = ("docker/compose.env", ".env", "local.env", "docker/local.env")
MAX_INDEX_BYTES = 64 * 1024


def _safe(name) -> bool:
    if not isinstance(name, str) or not name or "\\" in name or name.startswith("/"):
        return False
    parts = name.split("/")
    return all(p not in ("", ".", "..") for p in parts) and ":" not in parts[0]


def verify(folder) -> dict:
    """{name: sha256} of a bundle folder, after checking it completely. MetadataError if anything is
    wrong: the index unreadable or malformed, a file missing, extra or changed, an unsafe path, a
    required file absent, or a file that must never be bundled."""
    folder = Path(folder)
    try:
        raw = (folder / INDEX).read_bytes()
    except OSError:
        raise MetadataError(f"the deployment files have no {INDEX}") from None
    if len(raw) > MAX_INDEX_BYTES:
        raise MetadataError(f"{INDEX} is larger than allowed")
    try:
        info = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise MetadataError(f"{INDEX} isn't valid JSON") from None
    if not isinstance(info, dict) or info.get("schema") != SCHEMA:
        raise MetadataError(f"{INDEX} has an unknown schema")
    if info.get("platform") != "windows":
        raise MetadataError(f"{INDEX} is for another platform: {str(info.get('platform'))[:20]!r}")
    files = info.get("files")
    if not isinstance(files, dict) or not files:
        raise MetadataError(f"{INDEX} lists no files")
    for name, digest in files.items():
        if not _safe(name):
            raise MetadataError(f"{INDEX} has an unsafe path {str(name)[:80]!r}")
        if name in NEVER_BUNDLED or name.endswith(".env"):
            raise MetadataError(f"{name} must never be a deployment file")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise MetadataError(f"{INDEX} has a malformed hash for {name}")
    missing_required = [n for n in WINDOWS_FILES if n not in files]
    if missing_required:
        raise MetadataError(f"the deployment files lack {', '.join(missing_required)}")
    found = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()} - {INDEX}
    if found != set(files):
        raise MetadataError(f"the deployment files don't match {INDEX}: missing {sorted(set(files) - found)}, "
                            f"unexpected {sorted(found - set(files))}"[:300])
    for name, digest in files.items():
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != digest:
            raise MetadataError(f"{name} doesn't match its hash in {INDEX}")
    return dict(sorted(files.items()))


def read(folder) -> dict:
    """{name: bytes} of a verified bundle folder."""
    folder = Path(folder)
    return {name: (folder / name).read_bytes() for name in verify(folder)}
