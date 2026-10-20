from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import DiscordWebhook
from .serializers import DiscordWebhookSerializer
from .sender import send_discord_alert


class DiscordWebhookViewSet(viewsets.ModelViewSet):
    queryset = DiscordWebhook.objects.all().order_by("-created_at")
    serializer_class = DiscordWebhookSerializer

    @action(detail=True, methods=["post"])
    def test(self, request, pk=None):
        """Send a test message to verify the webhook works."""
        webhook = self.get_object()
        ok = send_discord_alert(
            webhook_url=webhook.url,
            handle="test.bsky.social",
            display_name="Test Account",
            text="This is a test announcement from your DRF bot.",
            post_url="https://bsky.app",
        )
        if ok:
            return Response({"status": "sent"})
        return Response({"status": "failed"}, status=status.HTTP_502_BAD_GATEWAY)
