"""Step 7: "Check all" as a background job (bluesky_monitor/jobs.py).

Bluesky and Discord are faked. The cross-process version (the job, a scheduled check and
two more Check All runs at once, each in its own process) is in tests_processes.py.
"""
import time
import uuid
from datetime import timedelta
from unittest import mock

from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from activity.models import ActivityEvent
from discord_alerts import channels
from discord_alerts.models import DiscordWebhook
from discord_alerts.sender import DeliveryResult
from . import jobs, locks, poller, scheduler
from .models import CheckJob, MonitoredAccount, SeenPost
from .tests import feed_item
from .tests_step6 import CREDS, PASSWORD, WEBHOOK, CacheMixin, FeedsByAccount, all_text

URL = "/api/accounts/check_all/"


class JobTestMixin(CacheMixin):
    def setUp(self):
        super().setUp()
        channel = DiscordWebhook.objects.create(name="#general", url=WEBHOOK)
        channels.make_default(channel)
        self.a = MonitoredAccount.objects.create(handle="a.bsky.social", first_poll_done=True)
        self.b = MonitoredAccount.objects.create(handle="b.bsky.social", first_poll_done=True)
        self.c = MonitoredAccount.objects.create(handle="c.bsky.social", first_poll_done=True)
        self.paused = MonitoredAccount.objects.create(handle="paused.bsky.social", is_active=False)
        patcher = mock.patch("discord_alerts.sender.deliver_alert", return_value=DeliveryResult(True))
        self.send = patcher.start()
        self.addCleanup(patcher.stop)
        self.session = FeedsByAccount({"a.bsky.social": [feed_item("a1")], "b.bsky.social": [feed_item("b1")],
                                       "c.bsky.social": []})
        patcher = mock.patch("bluesky_monitor.poller._new_session", return_value=self.session)
        patcher.start()
        self.addCleanup(patcher.stop)

    def queue(self):
        res = self.client.post(URL)
        self.assertEqual(res.status_code, 202, res.content)
        return res

    def run_jobs(self, instance="T"):
        self.assertTrue(locks.acquire_scheduler_lease(instance))
        return jobs.run_pending(instance)


@override_settings(**CREDS)
class CheckAllJobApiTests(JobTestMixin, APITestCase):
    def test_request_is_accepted_at_once_with_a_job_to_follow(self):
        res = self.queue()
        body = res.json()
        self.assertEqual(res["Location"], body["url"])
        self.assertEqual(body["url"], f"/api/check-jobs/{body['id']}/")
        uuid.UUID(body["id"])
        self.assertEqual((body["status"], body["final"], body["created"]), ("queued", False, True))
        self.assertEqual(body["poll_after_seconds"], 2)
        self.send.assert_not_called()
        self.assertEqual(self.session.read, [], "nothing is checked inside the web request")

    def test_queued_job_says_whether_the_checker_is_running(self):
        body = self.queue().json()
        self.assertFalse(body["checker_running"])
        self.assertIn("isn't running", body["message"])
        locks.acquire_scheduler_lease("T")
        body = self.client.get(body["url"]).json()
        self.assertTrue(body["checker_running"])
        self.assertEqual(body["message"], "The check starts in a moment.")

    def test_a_second_click_returns_the_same_job(self):
        first = self.queue().json()
        second = self.queue().json()
        self.assertEqual((second["id"], second["created"]), (first["id"], False))
        CheckJob.objects.filter(pk=first["id"]).update(status=CheckJob.RUNNING, worker="T")
        locks.acquire_scheduler_lease("T")
        self.assertEqual(self.queue().json()["id"], first["id"], "also while it runs")
        self.assertEqual(CheckJob.objects.count(), 1)

    def test_the_job_runs_in_the_checker_and_reports_every_account(self):
        job_id = self.queue().json()["id"]
        self.assertEqual(self.run_jobs(), 1)
        body = self.client.get(f"/api/check-jobs/{job_id}/").json()
        self.assertEqual((body["status"], body["final"], body["result"]), ("finished", True, "ok"))
        self.assertEqual(body["status_label"], "Done")
        self.assertEqual(body["progress"], {"total": 3, "done": 3, "percent": 100, "current": None})
        rows = {r["handle"]: (r["result"], r["reason"]) for r in body["accounts"]}
        self.assertEqual(rows, {"a.bsky.social": ("checked", None), "b.bsky.social": ("checked", None),
                                "c.bsky.social": ("checked", None), "paused.bsky.social": ("skipped", "paused")})
        self.assertEqual(body["counts"]["posts_sent"], 2)
        self.assertIsNone(body["poll_after_seconds"])
        self.assertIsNone(body["checker_running"])
        self.assertEqual(self.send.call_count, 2)

    def test_progress_is_visible_while_it_runs(self):
        seen = {}
        real_poll = poller.poll_account

        def poll(account, session=None):
            if account.handle == "b.bsky.social":
                seen.update(self.client.get(f"/api/check-jobs/{job_id}/").json())
            return real_poll(account, session=session)

        job_id = self.queue().json()["id"]
        with mock.patch("bluesky_monitor.poller.poll_account", side_effect=poll):
            self.run_jobs()
        self.assertEqual(seen["status"], "running")
        self.assertEqual(seen["status_label"], "Checking")
        self.assertEqual(seen["progress"]["done"], 1)
        self.assertEqual(seen["progress"]["current"]["handle"], "b.bsky.social")
        self.assertEqual(seen["progress"]["percent"], 33)
        self.assertIn("Checking @b.bsky.social", seen["message"])
        rows = [(r["handle"], r["result"]) for r in seen["accounts"]]
        self.assertEqual(rows, [("a.bsky.social", "checked"), ("b.bsky.social", "checking"),
                                ("c.bsky.social", "waiting"), ("paused.bsky.social", "skipped")])
        self.assertEqual(seen["counts"]["waiting"], 2)

    def test_results_are_still_there_after_a_refresh(self):
        job_id = self.queue().json()["id"]
        self.run_jobs()
        first = self.client.get(f"/api/check-jobs/{job_id}/").json()
        self.client = type(self.client)()  # a new browser tab, no state carried over
        latest = self.client.get("/api/check-jobs/latest/").json()
        self.assertEqual(latest, first)
        listed = self.client.get("/api/check-jobs/").json()["results"]
        self.assertEqual([j["id"] for j in listed], [job_id])

    def test_a_new_click_after_it_finished_starts_a_new_job(self):
        first = self.queue().json()["id"]
        self.run_jobs()
        second = self.queue().json()
        self.assertNotEqual(second["id"], first)
        self.assertTrue(second["created"])
        self.assertEqual(self.client.get("/api/check-jobs/latest/").json()["id"], second["id"])

    def test_only_the_checker_holding_the_lease_runs_jobs(self):
        self.queue()
        locks.acquire_scheduler_lease("OTHER")
        self.assertEqual(jobs.run_pending("STANDBY"), 0)
        self.assertEqual(CheckJob.objects.get().status, CheckJob.QUEUED)

    def test_a_job_is_claimed_only_once(self):
        self.queue()
        self.assertIsNotNone(jobs.claim_next("T"))
        self.assertIsNone(jobs.claim_next("U"))

    def test_queued_job_expires_when_no_checker_picks_it_up(self):
        job_id = self.queue().json()["id"]
        CheckJob.objects.filter(pk=job_id).update(requested_at=timezone.now() - timedelta(minutes=5))
        body = self.client.get(f"/api/check-jobs/{job_id}/").json()
        self.assertEqual((body["status"], body["final"], body["status_label"]), ("expired", True, "Didn't start"))
        self.assertIn("checker isn't running", body["message"])
        self.assertTrue(self.queue().json()["created"], "a new click queues a fresh job")

    def test_job_of_a_stopped_checker_becomes_interrupted(self):
        job_id = self.queue().json()["id"]
        rows = [{"id": self.a.pk, "handle": "a.bsky.social", "result": "checked", "reason": None},
                {"id": self.b.pk, "handle": "b.bsky.social", "result": "checking", "reason": None},
                {"id": self.c.pk, "handle": "c.bsky.social", "result": "waiting", "reason": None}]
        CheckJob.objects.filter(pk=job_id).update(status=CheckJob.RUNNING, worker="DEAD", accounts=rows,
                                                  started_at=timezone.now(), total=3, done=1)
        body = self.client.get(f"/api/check-jobs/{job_id}/").json()
        self.assertEqual((body["status"], body["final"], body["status_label"]), ("interrupted", True, "Stopped"))
        results = {r["handle"]: (r["result"], r["reason"]) for r in body["accounts"]}
        self.assertEqual(results, {"a.bsky.social": ("checked", None), "b.bsky.social": ("skipped", "stopped"),
                                   "c.bsky.social": ("skipped", "stopped")})

    def test_job_of_a_live_checker_keeps_running(self):
        job_id = self.queue().json()["id"]
        locks.acquire_scheduler_lease("LIVE")
        CheckJob.objects.filter(pk=job_id).update(status=CheckJob.RUNNING, worker="LIVE", started_at=timezone.now())
        self.assertEqual(self.client.get(f"/api/check-jobs/{job_id}/").json()["status"], "running")

    def test_lease_lost_mid_job_stops_the_rest(self):
        job_id = self.queue().json()["id"]
        real_poll = poller.poll_account

        def poll(account, session=None):
            locks.release_scheduler_lease("T")  # another checker takes over after the first account
            locks.acquire_scheduler_lease("NEW")
            return real_poll(account, session=session)

        with mock.patch("bluesky_monitor.poller.poll_account", side_effect=poll):
            self.run_jobs()
        job = CheckJob.objects.get(pk=job_id)
        self.assertIn(job.status, (CheckJob.FINISHED, CheckJob.INTERRUPTED))
        reasons = {r["handle"]: r["reason"] for r in job.accounts}
        self.assertEqual(reasons["b.bsky.social"], "stopped")
        self.assertEqual(reasons["c.bsky.social"], "stopped")

    def test_an_unexpected_error_ends_the_job_without_leaking_secrets(self):
        job_id = self.queue().json()["id"]
        with mock.patch("bluesky_monitor.check_all.run", side_effect=RuntimeError(f"bug {PASSWORD} {WEBHOOK}")):
            self.run_jobs()
        res = self.client.get(f"/api/check-jobs/{job_id}/")
        body = res.json()
        self.assertEqual((body["status"], body["result"], body["problem"]["category"]), ("finished", "failed", "internal"))
        self.assertNotIn(PASSWORD, res.content.decode())
        self.assertNotIn("hook-token", res.content.decode())
        self.assertNotIn(PASSWORD, all_text(list(ActivityEvent.objects.values())))
        self.assertNotIn("hook-token", all_text(list(ActivityEvent.objects.values())))

    def test_failed_accounts_never_leak_secrets_into_the_job(self):
        self.session.feeds["a.bsky.social"] = RuntimeError(f"boom {PASSWORD} {WEBHOOK}")
        job_id = self.queue().json()["id"]
        self.run_jobs()
        res = self.client.get(f"/api/check-jobs/{job_id}/")
        self.assertEqual({r["handle"]: r["result"] for r in res.json()["accounts"]}["a.bsky.social"], "failed")
        self.assertNotIn(PASSWORD, res.content.decode())
        self.assertNotIn("hook-token", res.content.decode())
        self.assertNotIn(PASSWORD, all_text(list(CheckJob.objects.values())))

    def test_job_and_scheduled_check_together_send_each_post_once(self):
        job_id = self.queue().json()["id"]
        real_poll = poller.poll_account

        def poll(account, session=None):
            if account.handle == "a.bsky.social" and not getattr(poll, "nested", False):
                poll.nested = True
                scheduler.run_cycle("T")  # the scheduled check runs while the job holds account a
            return real_poll(account, session=session)

        with mock.patch("bluesky_monitor.poller.poll_account", side_effect=poll):
            self.run_jobs()
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(SeenPost.objects.filter(status=SeenPost.SENT).count(), 2)
        self.assertEqual(CheckJob.objects.get(pk=job_id).status, CheckJob.FINISHED)

    @override_settings(CHECK_JOBS_KEPT=3)
    def test_only_the_newest_finished_jobs_are_kept(self):
        now = timezone.now()
        for i in range(5):
            CheckJob.objects.create(status=CheckJob.FINISHED, requested_at=now - timedelta(hours=i + 1))
        newest = self.queue().json()["id"]
        self.assertEqual(CheckJob.objects.exclude(status=CheckJob.QUEUED).count(), 3)
        self.assertTrue(CheckJob.objects.filter(pk=newest).exists())

    def test_unknown_jobs(self):
        res = self.client.get(f"/api/check-jobs/{uuid.uuid4()}/")
        self.assertEqual((res.status_code, res.json()["code"]), (404, "check_job_not_found"))
        res = self.client.get("/api/check-jobs/not-a-job-id/")
        self.assertEqual((res.status_code, res.json()["code"]), (404, "not_found"))
        res = self.client.get("/api/check-jobs/latest/")
        self.assertEqual((res.status_code, res.json()["code"]), (404, "no_check_jobs"))
        self.assertEqual(self.client.get("/api/check-jobs/").json(), {"results": []})

    def test_job_status_is_read_only(self):
        job_id = self.queue().json()["id"]
        for method in ("post", "put", "patch", "delete"):
            res = getattr(self.client, method)(f"/api/check-jobs/{job_id}/")
            self.assertEqual(res.status_code, 405, method)
        self.assertEqual(self.client.get(URL).status_code, 405)

    def test_list_limit(self):
        for i in range(3):
            CheckJob.objects.create(status=CheckJob.FINISHED, requested_at=timezone.now() - timedelta(hours=i))
        self.assertEqual(len(self.client.get("/api/check-jobs/?limit=2").json()["results"]), 2)
        self.assertEqual(len(self.client.get("/api/check-jobs/?limit=abc").json()["results"]), 3)


@override_settings(**CREDS, CHECK_JOB_POLL_SECONDS=0.05)
class JobWorkerThreadTests(JobTestMixin, TransactionTestCase):
    """The real background thread the checker starts (scheduler.JobWorker) picks the job up."""

    client_class = APIClient

    def test_worker_thread_runs_a_queued_job(self):
        self.assertTrue(locks.acquire_scheduler_lease("W"))
        job_id = self.queue().json()["id"]
        worker = scheduler.JobWorker("W")
        worker.start()
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if CheckJob.objects.get(pk=job_id).status not in CheckJob.ACTIVE:
                    break
                time.sleep(0.05)
        finally:
            worker.stop()
            worker.join(5)
        self.assertEqual(CheckJob.objects.get(pk=job_id).status, CheckJob.FINISHED)
        self.assertEqual(self.send.call_count, 2)
        self.assertFalse(worker.is_alive())
