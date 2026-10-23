"""Stopping the checker cleanly (shutdown.py, scheduler.run_forever).

In one process: once a stop is asked for, no new send, account or Check All job starts, the
send in progress finishes and is saved, and the wait for it is limited.

In real processes (Linux and macOS: SIGTERM is how Docker stops the checker; Windows has no
such signal): a checker stopped in the middle of a send finishes it and exits with nothing
left in "sending"; a send that hangs is given up after CHECKER_STOP_TIMEOUT_SECONDS, and the
stop says so; a second signal stops at once.
"""
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from discord_alerts.sender import DeliveryResult
from . import check_all, jobs, locks, poller, scheduler, shutdown
from .models import CheckJob, MonitoredAccount, PostDelivery, SchedulerState, SeenPost
from .tests import FakeSession, feed_item

HOOK = "https://discord.com/api/webhooks/1/default"


class StopRequestMixin:
    def setUp(self):
        super().setUp()
        shutdown.reset()
        self.addCleanup(shutdown.reset)


@override_settings(DISCORD_WEBHOOK_URL=HOOK)
class StopInOneProcessTests(StopRequestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.account = MonitoredAccount.objects.create(handle="artist.bsky.social", first_poll_done=True)
        self.sent = []
        p = mock.patch("discord_alerts.sender.deliver_alert", side_effect=self._deliver)
        p.start()
        self.addCleanup(p.stop)
        self.stop_after = None

    def _deliver(self, **kwargs):
        self.sent.append(kwargs["text"])
        if self.stop_after is not None and len(self.sent) >= self.stop_after:
            shutdown.request()  # the stop arrives while this send is in progress
        return DeliveryResult(True)

    def claim(self, account, *rkeys):
        for r in rkeys:
            self.assertTrue(poller.claim_post(account, feed_item(r, text=f"post {r}"), SeenPost.PENDING))

    def statuses(self):
        return dict(PostDelivery.objects.values_list("post__post_uri", "status"))

    def test_the_send_in_progress_finishes_and_nothing_new_starts(self):
        self.claim(self.account, "a", "b", "c")
        self.stop_after = 1
        self.assertEqual(poller.deliver_pending(self.account), 1)
        self.assertEqual(self.sent, ["post a"])
        statuses = sorted(self.statuses().values())
        self.assertEqual(statuses, ["pending", "pending", "sent"], "the started send is saved; the rest wait")
        self.assertFalse(PostDelivery.objects.filter(status=PostDelivery.SENDING).exists())
        self.assertEqual(sorted(PostDelivery.objects.values_list("attempts", flat=True)), [0, 0, 1],
                         "posts that weren't started aren't counted as tried")

    def test_the_waiting_posts_are_sent_after_the_next_start(self):
        self.claim(self.account, "a", "b")
        self.stop_after = 1
        poller.deliver_pending(self.account)
        shutdown.reset()
        self.stop_after = None
        self.assertEqual(poller.deliver_pending(self.account), 1)
        self.assertEqual(sorted(self.sent), ["post a", "post b"], "each post sent once")

    def test_a_check_stops_before_the_next_account(self):
        second = MonitoredAccount.objects.create(handle="second.bsky.social", first_poll_done=True)
        self.claim(self.account, "a")
        self.claim(second, "b")
        self.stop_after = 1
        session = FakeSession()
        summary, reports = poller.check_accounts([self.account, second], session=session)
        self.assertEqual(summary.result, "interrupted")
        self.assertEqual([r.reason for r in reports], ["", "stopped"])
        self.assertEqual(session.calls, 1, "the second account isn't read from Bluesky")
        self.assertEqual(self.sent, ["post a"])

    def test_a_running_check_all_job_saves_what_it_did(self):
        second = MonitoredAccount.objects.create(handle="second.bsky.social", first_poll_done=True)
        self.claim(self.account, "a")
        self.claim(second, "b")
        self.assertTrue(locks.acquire_scheduler_lease("checker-1"))
        job, _ = jobs.request_check_all()
        self.stop_after = 1
        with mock.patch.object(poller, "_new_session", return_value=FakeSession()):
            jobs.execute(jobs.claim_next("checker-1"), "checker-1")
        job.refresh_from_db()
        self.assertEqual(job.status, CheckJob.FINISHED, "the job isn't left running")
        self.assertEqual({row["handle"]: row["reason"] for row in job.accounts},
                         {"artist.bsky.social": None, "second.bsky.social": "stopped"})
        self.assertIn("1 account not reached", job.message)

    def test_no_new_check_all_job_starts_once_stopping(self):
        self.assertTrue(locks.acquire_scheduler_lease("checker-1"))
        job, _ = jobs.request_check_all()
        shutdown.request()
        self.assertEqual(jobs.run_pending("checker-1"), 0)
        job.refresh_from_db()
        self.assertEqual(job.status, CheckJob.QUEUED, "left for the next checker")

    def test_no_check_and_no_heartbeat_once_stopping(self):
        shutdown.request()
        self.assertFalse(scheduler.run_cycle("checker-1"))
        self.assertFalse(scheduler.heartbeat("checker-1"))
        self.assertFalse(SchedulerState.objects.filter(instance_id="checker-1").exists(),
                         "a stopping checker never takes the lease (back)")
        self.assertEqual(self.sent, [])


class StopWaitTests(StopRequestMixin, SimpleTestCase):
    def work(self, seconds):
        started = threading.Event()

        def run():
            with shutdown.busy():
                started.set()
                time.sleep(seconds)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        started.wait(5)
        return t

    def test_it_waits_for_the_work_in_progress(self):
        self.work(0.4)
        began = time.monotonic()
        self.assertTrue(scheduler.stop_gracefully(timeout=10))
        self.assertLess(time.monotonic() - began, 5)
        self.assertTrue(shutdown.requested())
        self.assertEqual(shutdown.busy_count(), 0)

    def test_it_never_waits_longer_than_the_limit(self):
        self.work(5)
        began = time.monotonic()
        self.assertFalse(scheduler.stop_gracefully(timeout=0.5))
        self.assertLess(time.monotonic() - began, 2)

    def test_with_nothing_in_progress_it_returns_at_once(self):
        began = time.monotonic()
        self.assertTrue(scheduler.stop_gracefully(timeout=10))
        self.assertLess(time.monotonic() - began, 1)

    def test_the_limit_fits_inside_dockers_stop_time(self):
        from django.conf import settings

        path = Path(settings.BASE_DIR) / "docker-compose.yml"
        if not path.exists():
            self.skipTest("docker-compose.yml isn't part of the image")
        compose = path.read_text(encoding="utf-8")
        checker = compose.split("  checker:", 1)[1]
        grace = int(checker.split("stop_grace_period:", 1)[1].split("s", 1)[0])
        self.assertLessEqual(settings.CHECKER_STOP_TIMEOUT_SECONDS + 5, grace,
                             "the checker must finish (or give up) before Docker kills it")


HELPER = Path(__file__).resolve().parent / "testing" / "checker_proc.py"


@unittest.skipIf(sys.platform == "win32", "Docker stops the checker with SIGTERM, which Windows doesn't have")
class StopSignalTests(SimpleTestCase):
    """A real checker process, stopped with SIGTERM in the middle of a fake Discord send."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.db, self.log = self.dir / "db.sqlite3", self.dir / "discord.log"
        self.env = {**os.environ, "DATABASE_PATH": str(self.db), "SECRET_KEY": "test-only-not-secret",
                    "BLUESKY_HANDLE": "bot.bsky.social", "BLUESKY_PASSWORD": "not-used", "DISCORD_WEBHOOK_URL": "",
                    "CHECK_JOB_POLL_SECONDS": "0.2", "SCHEDULER_WATCHDOG_SECONDS": "0", "BOT_LOG_LEVEL": "INFO",
                    "SETTINGS_ENV_MIRROR_PATH": "", "DATA_LOCK_WAIT_SECONDS": "5"}
        subprocess.run([sys.executable, str(HELPER), str(self.db), "setup", "2", "3"], env=self.env, check=True,
                       capture_output=True, timeout=120)

    def start(self, seconds_per_send, *mode, **env):
        return subprocess.Popen([sys.executable, str(HELPER), str(self.db), "run", str(self.log),
                                 str(seconds_per_send), *mode], env={**self.env, **env},
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def wait_for_log(self, text, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.log.exists() and text in self.log.read_text(encoding="utf-8"):
                return
            time.sleep(0.05)
        self.fail(f"never saw {text!r} in the fake Discord log")

    def stop(self, proc, timeout=60):
        began = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, time.monotonic() - began, out + err

    def deliveries(self):
        with closing(sqlite3.connect(self.db)) as con:
            return sorted(r[0] for r in con.execute("SELECT status FROM bluesky_monitor_postdelivery"))

    def test_a_stop_during_a_send_finishes_it_and_starts_no_other(self):
        proc = self.start(2)
        self.wait_for_log("start a0 post 0")
        code, took, output = self.stop(proc)
        self.assertEqual(code, 0, output)
        self.assertLess(took, 15)
        self.assertEqual(self.log.read_text(encoding="utf-8").split("\n").count("done a0 post 0"), 1)
        self.assertNotIn("start a0 post 1", self.log.read_text(encoding="utf-8"), "no new send after the stop")
        self.assertEqual(self.deliveries(), ["pending"] * 5 + ["sent"], "nothing is left in 'sending'")
        self.assertIn("The checker stopped cleanly", output)

    def test_a_stop_during_check_all_saves_the_job(self):
        proc = self.start(2, "check_all")
        self.wait_for_log("start a0 post 0")
        code, took, output = self.stop(proc)
        self.assertEqual(code, 0, output)
        self.assertEqual(self.deliveries(), ["pending"] * 5 + ["sent"])
        with closing(sqlite3.connect(self.db)) as con:
            status, message = con.execute("SELECT status, message FROM bluesky_monitor_checkjob").fetchone()
        self.assertEqual(status, "finished", "the job isn't cut off half way")
        self.assertIn("not reached", message)

    def test_a_send_that_hangs_is_given_up_after_the_limit(self):
        proc = self.start(60, CHECKER_STOP_TIMEOUT_SECONDS="1")
        self.wait_for_log("start a0 post 0")
        code, took, output = self.stop(proc)
        self.assertEqual(code, scheduler.STOP_TIMEOUT_EXIT, output)
        self.assertLess(took, 10, "the stop never hangs")
        self.assertIn("may appear twice in Discord", output)
        self.assertIn("sending", self.deliveries(), "the unfinished send is retried after the stale timeout")

    def test_a_second_signal_stops_at_once(self):
        proc = self.start(60)
        self.wait_for_log("start a0 post 0")
        proc.send_signal(signal.SIGTERM)
        time.sleep(0.5)
        began = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, scheduler.STOP_TIMEOUT_EXIT)
        self.assertLess(time.monotonic() - began, 5)

    def test_the_lease_is_free_after_a_clean_stop(self):
        proc = self.start(0.2)
        self.wait_for_log("done a0 post 0")
        code, _, output = self.stop(proc)
        self.assertEqual(code, 0, output)
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT instance_id FROM bluesky_monitor_schedulerstate").fetchone()[0], "")
