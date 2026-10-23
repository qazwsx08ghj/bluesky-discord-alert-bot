"""release/withdrawn.json: versions taken out of discovery. See docs/RELEASES.md, "Withdrawn releases".

{
  "schema": "alertbot-withdrawn/1",
  "revision": 3,                  # +1 on every change; never goes down
  "updated_at": "2026-11-02",
  "withdrawn": [
    {"version": "1.0.1", "withdrawn_at": "2026-11-02", "reason": "Plain words.", "replacement": "1.0.2"}
  ]
}

Withdrawing a version only removes it from what updaters offer. Its release, tag and image stay.
It never moves anyone to an older version.

Strict: anything wrong (unknown schema, a malformed or repeated version, a replacement that isn't
newer or is itself withdrawn) makes the whole file invalid, and updaters then install nothing.
"""
import re
from dataclasses import dataclass

from . import MetadataError, semver

SCHEMA = "alertbot-withdrawn/1"
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_REASON = 300
MAX_BYTES = 64 * 1024


@dataclass(frozen=True)
class Entry:
    version: semver.Version
    withdrawn_at: str
    reason: str
    replacement: semver.Version | None


@dataclass(frozen=True)
class Withdrawn:
    revision: int
    updated_at: str
    entries: dict  # Version -> Entry

    def get(self, version: semver.Version):
        return self.entries.get(version)

    def __contains__(self, version):
        return version in self.entries


def validate(data) -> Withdrawn:
    if not isinstance(data, dict):
        raise MetadataError("withdrawn.json isn't a JSON object")
    if data.get("schema") != SCHEMA:
        raise MetadataError(f"withdrawn.json has an unknown schema {data.get('schema')!r}"[:150])
    revision = data.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise MetadataError("withdrawn.json: revision must be a whole number, 1 or more")
    updated_at = data.get("updated_at")
    if not isinstance(updated_at, str) or not DATE.fullmatch(updated_at):
        raise MetadataError("withdrawn.json: updated_at must be a date (YYYY-MM-DD)")
    items = data.get("withdrawn")
    if not isinstance(items, list):
        raise MetadataError("withdrawn.json: withdrawn must be a list")
    entries = {}
    for i, item in enumerate(items):
        where = f"withdrawn.json entry {i + 1}"
        if not isinstance(item, dict):
            raise MetadataError(f"{where} isn't an object")
        version = semver.parse(item.get("version"))
        if version in entries:
            raise MetadataError(f"{where}: {version} is listed twice")
        when = item.get("withdrawn_at")
        if not isinstance(when, str) or not DATE.fullmatch(when):
            raise MetadataError(f"{where}: withdrawn_at must be a date (YYYY-MM-DD)")
        if when > updated_at:
            raise MetadataError(f"{where}: withdrawn_at is after the file's updated_at")
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > MAX_REASON:
            raise MetadataError(f"{where}: reason must be plain text of 1 to {MAX_REASON} characters")
        replacement = item.get("replacement")
        replacement = None if replacement is None else semver.parse(replacement)
        if replacement is not None and replacement <= version:
            raise MetadataError(f"{where}: the replacement must be newer than {version}")
        entries[version] = Entry(version, when, reason.strip(), replacement)
    for entry in entries.values():
        if entry.replacement is not None and entry.replacement in entries:
            raise MetadataError(f"withdrawn.json: {entry.version}'s replacement {entry.replacement} is withdrawn too")
    return Withdrawn(revision=revision, updated_at=updated_at, entries=entries)
