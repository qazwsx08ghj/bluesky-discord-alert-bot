"""Stopping the checker cleanly: finish the Discord send in progress, start nothing new.

When the checker is asked to stop (Docker sends SIGTERM on stop, restart and update):
1. request() sets a flag. From then on:
   - deliver_pending() starts no new send. A send already in progress finishes and its
     result is saved, so no delivery is left half done in "sending";
   - check_accounts() starts no new account (the rest are reported as "stopped");
   - the Check All worker starts no new job; a running one finishes at the next account
     and saves its result.
2. The checker waits for the work in progress (busy()) for at most
   CHECKER_STOP_TIMEOUT_SECONDS, then exits anyway, so a stop never hangs.

What this can't promise: a Discord send has no "undo" and no duplicate check. If the
checker is killed during a send (the time limit ran out, a crash, the computer lost power)
or Discord doesn't answer in time, the delivery stays in "sending" and is sent again after
SEND_STALE_SECONDS. If Discord did store the first one, that post arrives twice in that
channel. The stop log says how many sends were in that state.
"""
import threading
import time

_stop = threading.Event()
_lock = threading.Condition()
_busy = 0


def request() -> None:
    """Ask every checker loop in this process to stop at its next safe point."""
    _stop.set()


def requested() -> bool:
    return _stop.is_set()


def reset() -> None:
    """Forget a stop request (only for tests, and when a checker is started again in-process)."""
    _stop.clear()


class busy:
    """`with shutdown.busy():` marks work the checker should wait for before it exits."""

    def __enter__(self):
        global _busy
        with _lock:
            _busy += 1
        return self

    def __exit__(self, *exc):
        global _busy
        with _lock:
            _busy -= 1
            _lock.notify_all()
        return False


def busy_count() -> int:
    with _lock:
        return _busy


def wait_idle(timeout: float) -> bool:
    """Wait until no work is in progress, for at most timeout seconds. True if idle."""
    deadline = time.monotonic() + max(0.0, timeout)
    with _lock:
        while _busy > 0:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            _lock.wait(left)
        return True
