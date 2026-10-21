"""GET /api/health/ — is the control panel ready? Used by Docker and the .bat scripts.

200 when the database answers and is fully migrated, else 503. The checker's state is
included for information only: the panel works (and shows the problem) without it.
"""
from django.conf import settings
from django.db import connection
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
def health(request):
    from activity.status import scheduler_health
    from botconfig import store
    from botconfig.management.commands.backup_db import pending_migrations

    body = {"status": "ok", "version": settings.APP_VERSION, "database": False, "migrations_pending": None,
            "checker": None}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        body["database"] = True
        body["migrations_pending"] = len(pending_migrations())
        if body["migrations_pending"]:
            body["status"] = "updating"
        else:
            body["checker"] = scheduler_health(timezone.now(), store.poll_interval_minutes())["state"]
    except Exception:  # no database yet, or it is being updated
        body["status"] = "starting" if not body["database"] else "error"
    return Response(body, status=200 if body["status"] == "ok" else 503)
