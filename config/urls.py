from django.contrib import admin
from django.urls import path, include
from activity.views import status_view
from bluesky_monitor.views import profile_lookup
from .frontend_views import index, bot_settings, test_discord, test_login

urlpatterns = [
    path("", index, name="index"),
    path("admin/", admin.site.urls),
    path("api/accounts/", include("bluesky_monitor.urls")),
    path("api/webhooks/", include("discord_alerts.urls")),
    path("api/settings/", bot_settings, name="bot-settings"),
    path("api/settings/test-discord/", test_discord, name="test-discord"),
    path("api/settings/test-login/", test_login, name="test-login"),
    path("api/bluesky/profile/", profile_lookup, name="bluesky-profile"),
    path("api/activity/", include("activity.urls")),
    path("api/status/", status_view, name="bot-status"),
]
