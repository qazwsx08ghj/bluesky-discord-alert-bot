"""Scheduler lease, restarts, standby takeover, where the checker starts, and the migration."""
from datetime import datetime, timedelta, timezone as dt_tz
from unittest import mock

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings

from . import locks, scheduler
from .apps import should_start_in_process
from .models import MonitoredAccount, SchedulerState

T0 = datetime(2026, 10, 21, 12, 0, tzinfo=dt_tz.utc)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


@override_settings(SCHEDULER_LEASE_SECONDS=90)
class LeaseTests(TestCase):
    def setUp(self):
        self.clock = Clock()
        p = mock.patch("django.utils.timezone.now", self.clock)
        p.start()
        self.addCleanup(p.stop)

    def test_only_one_holder(self):
        self.assertTrue(locks.acquire_scheduler_lease("A"))
        self.assertFalse(locks.acquire_scheduler_lease("B"))
        self.assertTrue(locks.holds_scheduler_lease("A"))
        self.assertFalse(locks.holds_scheduler_lease("B"))

    def test_heartbeat_keeps_the_lease(self):
        locks.acquire_scheduler_lease("A")
        for _ in range(10):
            self.clock.now += timedelta(seconds=30)
            self.assertTrue(locks.acquire_scheduler_lease("A"))
            self.assertFalse(locks.acquire_scheduler_lease("B"))

    def test_stale_lease_is_taken_over_and_old_holder_knows(self):
        locks.acquire_scheduler_lease("A")
        self.clock.now += timedelta(seconds=91)  # A stopped renewing (crashed)
        self.assertFalse(locks.holds_scheduler_lease("A"))
        self.assertTrue(locks.acquire_scheduler_lease("B"))
        self.assertFalse(locks.acquire_scheduler_lease("A"), "a recovered A becomes standby")

    def test_clean_shutdown_releases_at_once(self):
        locks.acquire_scheduler_lease("A")
        locks.release_scheduler_lease("A")
        self.assertTrue(locks.acquire_scheduler_lease("B"))

    def test_release_by_non_holder_does_nothing(self):
        locks.acquire_scheduler_lease("A")
        locks.release_scheduler_lease("B")
        self.assertFalse(locks.acquire_scheduler_lease("B"))

    def test_restart_with_a_new_instance_id_waits_for_the_old_lease(self):
        """A restarted process gets a new id; after a crash it waits at most one lease period."""
        locks.acquire_scheduler_lease("host:100:aaaa")
        self.assertFalse(locks.acquire_scheduler_lease("host:200:bbbb"))
        self.clock.now += timedelta(seconds=91)
        self.assertTrue(locks.acquire_scheduler_lease("host:200:bbbb"))


@override_settings(ACCOUNT_LOCK_SECONDS=300)
class AccountLockTests(TestCase):
    def setUp(self):
        self.clock = Clock()
        p = mock.patch("django.utils.timezone.now", self.clock)
        p.start()
        self.addCleanup(p.stop)
        self.account = MonitoredAccount.objects.create(handle="a.bsky.social")

    def test_lock_release_and_stale_recovery(self):
        token = locks.acquire_account_lock(self.account.pk)
        self.assertIsNotNone(token)
        self.assertIsNone(locks.acquire_account_lock(self.account.pk))
        locks.release_account_lock(self.account.pk, "wrong-token")
        self.assertIsNone(locks.acquire_account_lock(self.account.pk), "only the owner can release")
        locks.release_account_lock(self.account.pk, token)
        token2 = locks.acquire_account_lock(self.account.pk)
        self.assertIsNotNone(token2)
        self.clock.now += timedelta(seconds=301)  # holder died
        self.assertIsNotNone(locks.acquire_account_lock(self.account.pk))


class CycleTests(TestCase):
    def test_cycle_runs_with_the_lease_and_records_timestamps(self):
        with mock.patch("bluesky_monitor.poller.poll_all_active_accounts") as poll:
            self.assertTrue(scheduler.run_cycle("A"))
        poll.assert_called_once()
        state = SchedulerState.objects.get(pk=1)
        self.assertIsNotNone(state.cycle_started_at)
        self.assertIsNotNone(state.cycle_finished_at)
        self.assertEqual(state.last_cycle_error, "")

    def test_standby_checker_does_not_poll(self):
        locks.acquire_scheduler_lease("A")
        with mock.patch("bluesky_monitor.poller.poll_all_active_accounts") as poll:
            self.assertFalse(scheduler.run_cycle("B"))
        poll.assert_not_called()

    def test_cycle_stops_if_the_lease_is_lost_midway(self):
        MonitoredAccount.objects.create(handle="a.bsky.social")
        MonitoredAccount.objects.create(handle="b.bsky.social")
        polled = []

        def poll(account, session=None):
            polled.append(account.handle)
            SchedulerState.objects.filter(pk=1).update(instance_id="someone-else")  # taken over

        with mock.patch("bluesky_monitor.poller.poll_account", side_effect=poll):
            scheduler.run_cycle("A")
        self.assertEqual(polled, ["a.bsky.social"])

    def test_cycle_error_is_recorded_and_the_checker_survives(self):
        with mock.patch("bluesky_monitor.poller.poll_all_active_accounts", side_effect=RuntimeError("boom")):
            self.assertTrue(scheduler.run_cycle("A"))
        self.assertEqual(SchedulerState.objects.get(pk=1).last_cycle_error, "boom")

    def test_jobs_are_in_memory_and_single_instance(self):
        sched = scheduler._build(scheduler.BackgroundScheduler, "A")
        jobs = {j.id: j for j in sched.get_jobs()}
        self.assertEqual(set(jobs), {"heartbeat", "poll_bluesky"})
        self.assertEqual(jobs["poll_bluesky"].max_instances, 1)
        self.assertTrue(jobs["poll_bluesky"].coalesce)
        self.assertEqual(type(sched._jobstores["default"]).__name__, "MemoryJobStore")


class StartInProcessTests(TestCase):
    def test_only_the_serving_runserver_process_starts_the_checker(self):
        cases = [
            (["manage.py", "runserver"], {"RUN_MAIN": "true"}, True),
            (["manage.py", "runserver"], {}, False),                       # auto-reload parent
            (["manage.py", "runserver", "--noreload"], {}, True),
            (["manage.py", "runserver"], {"RUN_MAIN": "true", "SCHEDULER_IN_WEB": "0"}, False),
            (["manage.py", "migrate"], {}, False),
            (["manage.py", "test"], {}, False),
            (["manage.py", "shell"], {}, False),
            (["manage.py", "run_scheduler"], {}, False),                   # starts its own
            (["gunicorn", "config.wsgi"], {}, False),
        ]
        for argv, env, expected in cases:
            with self.subTest(argv=argv, env=env):
                self.assertEqual(should_start_in_process(argv, env), expected)


class MigrationSafetyTests(TransactionTestCase):
    """Posts recorded by older versions must never be sent again after upgrading."""

    def test_existing_seen_posts_are_marked_sent(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("bluesky_monitor", "0001_initial")])
        old = executor.loader.project_state([("bluesky_monitor", "0001_initial")]).apps
        Account = old.get_model("bluesky_monitor", "MonitoredAccount")
        Seen = old.get_model("bluesky_monitor", "SeenPost")
        acc = Account.objects.create(handle="a.bsky.social", first_poll_done=True)
        Seen.objects.create(account=acc, post_uri="at://x/app.bsky.feed.post/1", post_cid="c")

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        from .models import SeenPost
        post = SeenPost.objects.get()
        self.assertEqual(post.status, SeenPost.SENT)
        self.assertEqual(MonitoredAccount.objects.get().handle, "a.bsky.social")
