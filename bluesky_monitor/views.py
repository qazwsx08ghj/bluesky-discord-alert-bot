from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view
from rest_framework.response import Response

from activity import log as activity, problems
from . import account_status, check_all as check_all_run, profiles
from .models import MonitoredAccount
from .serializers import MonitoredAccountSerializer
from .bluesky import BlueskyLoginError
from .poller import AccountBusy, poll_account


class MonitoredAccountViewSet(viewsets.ModelViewSet):
    queryset = MonitoredAccount.objects.select_related("channel").order_by("-created_at")
    serializer_class = MonitoredAccountSerializer
    lookup_value_regex = r"\d+"  # keeps /check_all/ apart from account ids

    def perform_create(self, serializer):
        account = serializer.save()
        activity.record(activity.ACCOUNT_ADDED, "info", f"Started watching @{account.handle}. "
                        "The first check notes its existing posts without sending them.", account=account)

    def perform_destroy(self, instance):
        handle, pk = instance.handle, instance.pk
        instance.delete()
        instance.pk = pk  # keep the id on the log entry
        activity.record(activity.ACCOUNT_REMOVED, "info", f"Stopped watching @{handle}.", account=instance)

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
            body.update(new_posts_announced=0, error=str(exc),
                        problem=problems.describe(getattr(exc, "category", "")))
        # Added in step 6: the account's status after the check, and what the check did.
        account.refresh_from_db()
        report = getattr(account, "check_report", None)
        info = account_status.describe(account)
        body.update(state=info["state"], state_label=info["state_label"], state_message=info["state_message"],
                    first_check=info["first_check"], problem=body.get("problem") or info["problem"])
        if report is not None and hasattr(report, "as_dict"):
            body["check"] = {"result": report.result, "message": check_all_run.account_message(report),
                             "new_posts": report.new_posts, "posts_sent": report.posts_sent,
                             "not_announced": report.turned_off + report.no_channel}
        return Response({"new_posts_announced": body["new_posts_announced"], **body}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"])
    def check_all(self, request):
        """Check every account now; per-account results. See bluesky_monitor/check_all.py."""
        return Response(check_all_run.run())


@api_view(["GET"])
def profile_lookup(request):
    """GET /api/bluesky/profile/?handle=… — see bluesky_monitor/profiles.py for the answers."""
    http_status, body = profiles.lookup(request.query_params.get("handle", ""))
    response = Response(body, status=http_status)
    if body.get("retry_after_seconds"):
        response["Retry-After"] = str(body["retry_after_seconds"])
    response["Cache-Control"] = "no-store"
    return response
