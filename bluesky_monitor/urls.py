from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import MonitoredAccountViewSet

router = DefaultRouter()
router.register(r"", MonitoredAccountViewSet, basename="monitored-account")

urlpatterns = [
    path("", include(router.urls)),
]
