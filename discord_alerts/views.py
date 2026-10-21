from django.db.models import Count
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from activity import log as activity
from . import channels
from .models import DiscordWebhook
from .sender import send_discord_alert
from .serializers import DiscordWebhookSerializer


@method_decorator(sensitive_post_parameters(), name="dispatch")
class DiscordWebhookViewSet(viewsets.ModelViewSet):
    """Named Discord channels. Routes are unchanged from before step 4.

    DELETE refuses (409) while accounts use the channel or it is the default, unless
    `?reassign_to=<channel id>` or `?reassign_to=default` says where they should go.
    """
    queryset = DiscordWebhook.objects.annotate(account_count=Count("accounts")).order_by("-created_at")
    serializer_class = DiscordWebhookSerializer

    def destroy(self, request, *args, **kwargs):
        channel = self.get_object()
        reassign = request.query_params.get("reassign_to")
        target = None
        if reassign == "default":
            target = "default"
        elif reassign:
            target = DiscordWebhook.objects.filter(pk=int(reassign)).first() if reassign.isdigit() else None
            if target is None:
                return Response({"code": "invalid_reassign", "message": "The channel to move to doesn't exist."},
                                status=status.HTTP_400_BAD_REQUEST)
        try:
            channels.delete_channel(channel, reassign_to=target)
        except channels.ChannelError as exc:
            return Response(exc.as_response_body(), status=exc.status)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def make_default(self, request, pk=None):
        """Make this the default channel. Accounts with their own channel keep it."""
        channel = channels.make_default(self.get_object())
        return Response(self.get_serializer(self.get_queryset().get(pk=channel.pk)).data)

    @action(detail=True, methods=["post"])
    def test(self, request, pk=None):
        """Send a test message to verify the webhook works."""
        webhook = self.get_object()
        ok = send_discord_alert(
            webhook_url=webhook.url,
            handle="test.bsky.social",
            display_name="Test Account",
            text="This is a test message from your Bluesky → Discord Alert Bot. If you can see it, this channel is set up correctly.",
            post_url="https://bsky.app",
        )
        channels.record_delivery(webhook.pk, ok, "" if ok else "The test message wasn't accepted by Discord.")
        if ok:
            activity.record(activity.CHANNEL_TEST_SENT, "success", f'Test message sent to "{webhook.name}".',
                            channel=webhook)
        else:
            activity.problem(activity.CHANNEL_TEST_FAILED, "discord_test_failed",
                             f'The test message to "{webhook.name}" wasn\'t delivered.', channel=webhook)
        if ok:
            return Response({"status": "sent"})
        return Response({"status": "failed"}, status=status.HTTP_502_BAD_GATEWAY)
