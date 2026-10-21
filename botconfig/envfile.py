"""Reading and safely rewriting the .env file.

Since step 3 the database holds the settings. The .env file is only kept up to date
(a "mirror") so that going back to older code still finds the current settings. See
docs/SETTINGS_MIGRATION.md.

Writes are atomic: the new content goes to a temporary file in the same folder, which
then replaces .env in one step. A failed write leaves the old .env exactly as it was.
Lines and keys this module doesn't manage (SECRET_KEY, comments, ...) are kept.
"""
import os
import re
import tempfile
from pathlib import Path

from dotenv import dotenv_values

STAMP_KEY = "BOT_SETTINGS_VERSION"
MANAGED_NOTE = (
    "# The settings below are saved in the bot's database and changed in the control panel.\n"
    "# They are copied here only so older versions of the bot keep working.\n"
)


def read(path: Path) -> dict:
    """Values as python-dotenv (and so the pre-step-3 code) reads them."""
    if not path or not Path(path).exists():
        return {}
    return {k: (v or "") for k, v in dotenv_values(path, encoding="utf-8").items()}


def _quote(value: str) -> str:
    """Write the value so python-dotenv reads back exactly the same string."""
    if value == "" or re.fullmatch(r"[A-Za-z0-9_\-.:/@+=,?&%~]+", value):
        return value
    if "'" not in value and "\n" not in value:
        return f"'{value}'"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def write(path: Path, values: dict) -> None:
    """Set the given keys in .env, keeping every other line. Atomic; raises OSError on failure."""
    path = Path(path)
    raw = path.read_bytes() if path.exists() else b""
    newline = "\r\n" if b"\r\n" in raw else "\n"
    lines = raw.decode("utf-8").splitlines()

    remaining = dict(values)
    out = []
    for line in lines:
        match = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        key = match.group(1) if match else None
        if key in remaining:
            out.append(f"{key}={_quote(remaining.pop(key))}")
        elif key in values:
            continue  # a duplicate of a key already written; drop it so only one value exists
        else:
            out.append(line)
    if remaining:
        if out and out[-1].strip():
            out.append("")
        if not any(line.startswith(MANAGED_NOTE.splitlines()[0]) for line in out):
            out.extend(MANAGED_NOTE.rstrip("\n").split("\n"))
        out.extend(f"{k}={_quote(v)}" for k, v in remaining.items())

    data = (newline.join(out) + newline).encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=".env.tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
