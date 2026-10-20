from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import DiscordWebhookViewSet

router = DefaultRouter()
router.register(r"", DiscordWebhookViewSet, basename="discord-webhook")

urlpatterns = [
    path("", include(router.urls)),
]
