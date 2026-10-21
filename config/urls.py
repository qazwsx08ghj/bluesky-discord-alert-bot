from django.urls import path, include
from activity.views import status_view
from bluesky_monitor.views import check_job_detail, check_job_latest, check_job_list, profile_lookup
from .frontend_views import index, bot_settings, test_discord, test_login
from .health import health
from .security import csrf_token

urlpatterns = [
    path("", index, name="index"),
    path("api/csrf/", csrf_token, name="csrf-token"),
    path("api/health/", health, name="health"),
    path("api/accounts/", include("bluesky_monitor.urls")),
    path("api/webhooks/", include("discord_alerts.urls")),
    path("api/settings/", bot_settings, name="bot-settings"),
    path("api/settings/test-discord/", test_discord, name="test-discord"),
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
