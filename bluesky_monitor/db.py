"""SQLite settings so the web server and the checker can share the database safely.

WAL mode lets readers and one writer work at the same time; busy_timeout makes a
writer wait for a moment instead of failing when another process is writing.
"""
from django.db.backends.signals import connection_created
from django.dispatch import receiver


@receiver(connection_created)
def configure_sqlite(sender, connection, **kwargs):
    if connection.vendor != "sqlite":
        return
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=20000;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
