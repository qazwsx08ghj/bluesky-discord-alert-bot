"""gunicorn settings for the web service (docker/entrypoint.sh starts gunicorn with -c this file).

While the web service runs, its main gunicorn process holds the shared data lock
(botconfig/datalock.py), so the database can't be migrated or restored under it. The lock is
taken before any worker starts and is dropped by the kernel when gunicorn ends, however it ends.
If the data stays busy (a migration or restore by another container), gunicorn doesn't start;
Docker then starts the web service again.
"""
_data_lock = None


def on_starting(server):
    global _data_lock
    from botconfig import datalock

    lock = datalock.DataLock(exclusive=False)
    if not lock.acquire(timeout=datalock.wait_seconds()):
        raise SystemExit("The database is being updated or restored by another part of the bot; "
                         "the control panel starts again in a moment.")
    _data_lock = lock
