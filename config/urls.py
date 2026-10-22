from django.urls import include, path, re_path
from activity.views import status_view
from bluesky_monitor.views import check_job_detail, check_job_latest, check_job_list, profile_lookup
from . import frontend
from .frontend_views import bot_settings, test_login
from .health import health
from .security import csrf_token

urlpatterns = [
    # The React control panel: one page for every panel address; the browser shows the right screen.
    path("", frontend.app, name="index"),
    re_path(r"^(?:setup|accounts|activity|settings)/?$", frontend.app, name="panel"),
    path("assets/<str:name>", frontend.asset, name="panel-asset"),
    path("favicon.svg", frontend.favicon, name="favicon"),
    path("third-party-notices.txt", frontend.third_party_notices, name="third-party-notices"),
    path("api/csrf/", csrf_token, name="csrf-token"),
    path("api/health/", health, name="health"),
    path("api/accounts/", include("bluesky_monitor.urls")),
    path("api/webhooks/", include("discord_alerts.urls")),
    path("api/settings/", bot_settings, name="bot-settings"),
    path("api/settings/test-login/", test_login, name="test-login"),
    path("api/bluesky/profile/", profile_lookup, name="bluesky-profile"),
    path("api/check-jobs/", check_job_list, name="check-job-list"),
    path("api/check-jobs/latest/", check_job_latest, name="check-job-latest"),
    path("api/check-jobs/<uuid:job_id>/", check_job_detail, name="check-job-detail"),
    path("api/activity/", include("activity.urls")),
    path("api/status/", status_view, name="bot-status"),
]

handler404 = "config.security.page_not_found"
handler500 = "config.security.server_error"
