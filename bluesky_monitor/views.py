from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import MonitoredAccount
from .serializers import MonitoredAccountSerializer
from .bluesky import BlueskyLoginError
from .poller import AccountBusy, poll_account


class MonitoredAccountViewSet(viewsets.ModelViewSet):
    queryset = MonitoredAccount.objects.all().order_by("-created_at")
    serializer_class = MonitoredAccountSerializer

    @action(detail=True, methods=["post"])
    def poll_now(self, request, pk=None):
        account = self.get_object()
        was_first = not account.first_poll_done
        body = {"was_first_poll": was_first}
        try:
            body["new_posts_announced"] = poll_account(account)
        except AccountBusy:
            return Response({
                "code": "already_checking",
                "message": "This account is already being checked. Try again in a moment.",
            }, status=status.HTTP_409_CONFLICT)
        except BlueskyLoginError as exc:
            # Same response as before (nothing announced), plus the reason.
            body.update(new_posts_announced=0, error=str(exc))
        return Response({"new_posts_announced": body["new_posts_announced"], **body}, status=status.HTTP_200_OK)
