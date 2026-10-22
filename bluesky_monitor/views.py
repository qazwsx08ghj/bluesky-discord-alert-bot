from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view
from rest_framework.response import Response

from activity import log as activity, problems
from . import account_status, check_all as check_all_run, jobs, profiles
from .models import CheckJob, MonitoredAccount, SeenPost
from .serializers import MonitoredAccountSerializer, SeenPostSerializer
from .bluesky import BlueskyLoginError
from .poller import AccountBusy, poll_account


class MonitoredAccountViewSet(viewsets.ModelViewSet):
    queryset = MonitoredAccount.objects.prefetch_related("destinations__channel").order_by("-created_at")
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
        # Also: the account's status after the check, and what the check did.
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

    @action(detail=True, methods=["get"])
    def posts(self, request, pk=None):
        """GET /api/accounts/{id}/posts/ — the posts noticed for this account, newest first,
        20 per page (?page=2). Replaces the nested `seen_posts` list that older versions returned."""
        account = self.get_object()
        queryset = (SeenPost.objects.filter(account=account).prefetch_related("deliveries__channel")
                    .order_by("-seen_at", "-id"))
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(SeenPostSerializer(page, many=True).data)

    @action(detail=False, methods=["post"])
    def check_all(self, request):
        """Queue a check of every account; the checker runs it in the background (jobs.py).

        202 with the job. If a Check All is already queued or running, that same job is
        returned (created: false) instead of starting another. Follow `url` for progress.
        """
        job, created = jobs.request_check_all()
        body = {**jobs.describe(job), "created": created}
        return Response(body, status=status.HTTP_202_ACCEPTED, headers={"Location": body["url"]})


JOB_NOT_FOUND = {"code": "check_job_not_found",
                 "message": "That check isn't known. It may be too old: only the newest checks are kept."}


@api_view(["GET"])
def check_job_list(request):
    """GET /api/check-jobs/?limit=10 — the newest Check All jobs first (at most 50)."""
    try:
        limit = max(1, min(50, int(request.query_params.get("limit", 10))))
    except ValueError:
        limit = 10
    jobs.tidy()
    checker = jobs.checker_running()
    rows = CheckJob.objects.order_by("-requested_at")[:limit]
    return Response({"results": [jobs.describe(j, checker=checker) for j in rows]},
                    headers={"Cache-Control": "no-store"})


@api_view(["GET"])
def check_job_latest(request):
    """GET /api/check-jobs/latest/ — the newest job, so the panel can show it again after a refresh."""
    jobs.tidy()
    job = CheckJob.objects.order_by("-requested_at").first()
    if job is None:
        return Response({"code": "no_check_jobs", "message": "Check all hasn't been used yet."}, status=404)
    return Response(jobs.describe(job))


@api_view(["GET"])
def check_job_detail(request, job_id):
    """GET /api/check-jobs/{id}/ — progress while queued or running, then the per-account results."""
    jobs.tidy()
    job = CheckJob.objects.filter(pk=job_id).first()
    if job is None:
        return Response(JOB_NOT_FOUND, status=404)
    return Response(jobs.describe(job))


@api_view(["GET"])
def profile_lookup(request):
    """GET /api/bluesky/profile/?handle=… — see bluesky_monitor/profiles.py for the answers."""
    http_status, body = profiles.lookup(request.query_params.get("handle", ""))
    response = Response(body, status=http_status)
    if body.get("retry_after_seconds"):
        response["Retry-After"] = str(body["retry_after_seconds"])
    response["Cache-Control"] = "no-store"
    return response
