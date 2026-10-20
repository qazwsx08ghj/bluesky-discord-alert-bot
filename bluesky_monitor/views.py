from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import MonitoredAccount
from .serializers import MonitoredAccountSerializer
from .poller import poll_account


class MonitoredAccountViewSet(viewsets.ModelViewSet):
    queryset = MonitoredAccount.objects.all().order_by("-created_at")
    serializer_class = MonitoredAccountSerializer

    @action(detail=True, methods=["post"])
    def poll_now(self, request, pk=None):
        account = self.get_object()
        was_first = not account.first_poll_done
        new_posts = poll_account(account)
        return Response({
            "new_posts_announced": new_posts,
            "was_first_poll": was_first,
        }, status=status.HTTP_200_OK)
