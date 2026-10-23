"""Who may use the database right now: the running bot, or a migration or restore.

The running bot holds a shared lock for as long as it runs: the web service (gunicorn's main
process, config/gunicorn_conf.py) and the checker (scheduler.run_forever). Changing the
database as a whole needs the exclusive lock, so it waits until every part of the bot has
stopped, and nothing of the bot can start while it is held:
- prepare_data, when it imports an old install or applies migrations;
- restore_db, when it puts a backup in place.

The lock is a kernel lock (flock) on DATA_LOCK_FILE_NAME next to the database. It belongs to
a process, not to the file: when a process ends in any way (stopped, killed, crashed, its
container removed), the kernel drops its lock at once. So there are no stale locks to clean
up, and the file itself is never deleted. Every container that mounts the data volume shares
one kernel (Docker's Linux VM), so they all see the same locks.

It is one of two safeguards. The Windows scripts also check that no container is using the
data volume before a restore (common.bat :ensure_data_free). That also covers 1.0.0-rc.2 and
older, which don't take this lock.

Without flock (Windows, used only by the development setup without Docker) acquire() always
succeeds and `supported` is False.
"""
import errno
import logging
import os
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

logger = logging.getLogger(__name__)

DATA_LOCK_FILE_NAME = ".data-access.lock"
BUSY_EXIT = 75  # exit code when the data stays busy (EX_TEMPFAIL); Docker then starts the service again


def database_path() -> Path | None:
    """The database file: from Django's settings once they are loaded, else from DATABASE_PATH
    (gunicorn's main process takes the lock before Django is set up)."""
    path = None
    try:
        from django.conf import settings

        if settings.configured:
            path = settings.DATABASES["default"]["NAME"]
    except Exception:
        path = None
    path = path or os.environ.get("DATABASE_PATH")
    if not path or str(path) == ":memory:" or "mode=memory" in str(path):
        return None
    return Path(path)


def lock_file(db_path=None) -> Path | None:
    db = Path(db_path) if db_path else database_path()
    return db.parent / DATA_LOCK_FILE_NAME if db else None


class DataLock:
    """`DataLock(exclusive=True).acquire(timeout)` -> True once held. release() (or process exit) frees it."""

    def __init__(self, exclusive: bool, db_path=None):
        self.exclusive = exclusive
        self.path = lock_file(db_path)
        self.supported = fcntl is not None and self.path is not None
        self._fd = None
        self._fake_held = False  # without flock: "held" between acquire() and release()

    @property
    def held(self) -> bool:
        return self._fd is not None or self._fake_held

    def acquire(self, timeout: float = 0, poll: float = 0.2) -> bool:
        if not self.supported:
            self._fake_held = True
            return True
        if self._fd is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        mode = (fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            try:
                fcntl.flock(fd, mode)
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                    os.close(fd)
                    raise
                if time.monotonic() >= deadline:
                    os.close(fd)
                    return False
                time.sleep(min(poll, max(0.01, deadline - time.monotonic())))
                continue
            self._fd = fd
            return True

    def release(self) -> None:
        self._fake_held = False
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def wait_seconds() -> float:
    """How long a migration or a starting service waits for the data (DATA_LOCK_WAIT_SECONDS)."""
    return float(os.environ.get("DATA_LOCK_WAIT_SECONDS", "180"))
