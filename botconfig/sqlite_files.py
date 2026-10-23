"""SQLite database files: checked, self-contained copies, and their journal files.

The bot's database runs in WAL mode: recent changes can sit in a "-wal" journal file next to
the database (with a "-shm" index), and SQLite applies that journal whenever the database is
opened. Two rules follow:
- A journal must never be applied to a file it doesn't belong to. Before a file is put in
  place of another (a restore, an import), the journals of both names are removed.
- Copies (backups, restores, imports) are made self-contained: one file, in rollback-journal
  mode, with no journal beside it. A copy can then be moved, exported to Windows, or opened
  read-only without SQLite creating or needing anything next to it.
"""
import hashlib
import sqlite3
from pathlib import Path

JOURNAL_SUFFIXES = ("-wal", "-shm", "-journal")


def journal_files(path) -> list[Path]:
    """The journal files next to path that exist right now."""
    return [p for p in (Path(f"{path}{s}") for s in JOURNAL_SUFFIXES) if p.exists()]


def remove_journal_files(path) -> None:
    for part in journal_files(path):
        part.unlink(missing_ok=True)


def open_read_only(path) -> sqlite3.Connection:
    """Open a file without writing anything, not even a lock or journal file next to it.

    `immutable=1` tells SQLite nobody changes the file meanwhile: use it only for copies and
    backups, never for the live database.
    """
    return sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro&immutable=1", uri=True)


def standalone_copy(source: sqlite3.Connection, target: Path) -> str:
    """Copy an open database into target with SQLite's backup API, as one self-contained file.

    Returns the result of SQLite's integrity check ("ok" when the copy is good). target and
    its journal files are replaced; the caller removes target if the result isn't "ok".
    """
    target.unlink(missing_ok=True)
    remove_journal_files(target)
    dst = sqlite3.connect(str(target))
    try:
        source.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")  # one file: no -wal or -shm beside it
        check = dst.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.DatabaseError:
        check = "unreadable"
    finally:
        dst.close()
    remove_journal_files(target)
    return check


def make_standalone(path: Path) -> None:
    """Fold any journal into the file itself and switch it to rollback-journal mode."""
    con = sqlite3.connect(str(path))
    try:
        con.execute("PRAGMA journal_mode=DELETE")
    finally:
        con.close()
    remove_journal_files(path)


def sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def applied_migrations(path) -> set[tuple[str, str]] | None:
    """(app, name) of every migration recorded in a copy (empty set: none yet).

    None if the file can't be read or has no migration table (it isn't a Django database).
    """
    try:
        con = open_read_only(path)
    except sqlite3.Error:
        return None
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "django_migrations" not in tables:
            return None
        return {(app, name) for app, name in con.execute("SELECT app, name FROM django_migrations")}
    except sqlite3.DatabaseError:
        return None
    finally:
        con.close()
