"""An older version never runs on data a newer version has updated (botconfig/compat.py).

"Newer data" is made here the way a newer version leaves it: a migration recorded in the
database that this version doesn't have. Every place that starts work is checked: the web
start (prepare_data), the scripts' check (check_compat), the checker (wait_for_database and
every scheduled check), and the warning page the web service shows instead of the panel.
"""
import tempfile
from io import StringIO
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.test import TestCase, override_settings

from bluesky_monitor import scheduler
from bluesky_monitor.models import MonitoredAccount, SchedulerState
from . import compat

FUTURE = ("bluesky_monitor", "9999_from_a_newer_version")


class NewerDataMixin:
    def setUp(self):
        super().setUp()
        compat._known = None
        self.addCleanup(setattr, compat, "_known", None)

    def make_newer(self):
        MigrationRecorder(connection).record_applied(*FUTURE)


class DetectionTests(NewerDataMixin, TestCase):
    def test_this_versions_own_data_is_fine(self):
        self.assertEqual(compat.unknown_migrations(), [])

    def test_a_migration_from_a_newer_version_is_found(self):
        self.make_newer()
        self.assertEqual(compat.unknown_migrations(), ["bluesky_monitor.9999_from_a_newer_version"])

    def test_apps_this_version_no_longer_has_are_ignored(self):
        """Old installs still have records of Django's admin app, which 1.0.0 removed."""
        MigrationRecorder(connection).record_applied("admin", "0001_initial")
        self.assertEqual(compat.unknown_migrations(), [])

    def test_migrations_replaced_by_a_squashed_one_count_as_known(self):
        loader = SimpleNamespace(disk_migrations={
            ("bluesky_monitor", "0010_squashed"): SimpleNamespace(replaces=[("bluesky_monitor", "0009_old")])})
        with mock.patch("botconfig.compat.MigrationLoader", return_value=loader):
            self.assertIn(("bluesky_monitor", "0009_old"), compat.known_migrations())

    def test_the_explanation_says_what_to_do(self):
        text = compat.explanation(["bluesky_monitor.9999_x"])
        for words in ("older version", "won't run", "start.bat in the folder of your newest download",
                      "restore.bat", "db-before-update", "bluesky_monitor.9999_x"):
            self.assertIn(words, text)


class WebStartTests(NewerDataMixin, TestCase):
    """prepare_data runs before the panel on every start, and before the backup and migrations."""

    def test_newer_data_stops_before_the_backup_and_the_migrations(self):
        self.make_newer()
        accounts_before = MonitoredAccount.objects.count()
        with tempfile.TemporaryDirectory() as backups, override_settings(BACKUP_DIR=backups), \
                mock.patch("botconfig.management.commands.prepare_data.call_command") as commands:
            err = StringIO()
            with self.assertRaises(SystemExit) as stopped:
                call_command("prepare_data", stdout=StringIO(), stderr=err)
            self.assertEqual(stopped.exception.code, compat.NEWER_DATA_EXIT)
            commands.assert_not_called()  # neither backup_db nor migrate
            from pathlib import Path
            self.assertEqual(list(Path(backups).iterdir()), [], "no backup is written")
        self.assertIn(compat.TITLE, err.getvalue())
        self.assertEqual(MonitoredAccount.objects.count(), accounts_before)
        self.assertIn(FUTURE, MigrationRecorder(connection).applied_migrations(), "the record is left as it is")

    def test_this_versions_data_starts_normally(self):
        with tempfile.TemporaryDirectory() as backups, override_settings(BACKUP_DIR=backups), \
                mock.patch("botconfig.management.commands.prepare_data.call_command") as commands:
            call_command("prepare_data", stdout=StringIO(), stderr=StringIO())
        self.assertEqual([c.args[0] for c in commands.call_args_list], ["backup_db", "migrate"])


class ScriptCheckTests(NewerDataMixin, TestCase):
    """check_compat: what start.bat, update.bat and setup.bat ask before they replace the bot."""

    def test_newer_data_answers_3(self):
        self.make_newer()
        err = StringIO()
        with self.assertRaises(SystemExit) as stopped:
            call_command("check_compat", stdout=StringIO(), stderr=err)
        self.assertEqual(stopped.exception.code, 3)
        self.assertIn(compat.TITLE, err.getvalue())

    def test_this_versions_data_answers_0(self):
        out = StringIO()
        call_command("check_compat", stdout=out)
        self.assertIn("can use the data", out.getvalue())


class CheckerTests(NewerDataMixin, TestCase):
    def test_the_checker_never_starts_on_newer_data_and_doesnt_give_up(self):
        """It says so once and keeps checking, with no timeout (no restart loop), never 'ready'."""
        self.make_newer()
        naps = []

        def sleep(seconds):
            naps.append(seconds)
            if len(naps) == 3:
                raise KeyboardInterrupt  # end the test's endless wait
        out, err = StringIO(), StringIO()
        with mock.patch("bluesky_monitor.management.commands.wait_for_database.time.sleep", side_effect=sleep), \
                self.assertRaises(KeyboardInterrupt):
            call_command("wait_for_database", "--timeout", "0", "--newer-interval", "60", stdout=out, stderr=err)
        self.assertEqual(naps, [60, 60, 60], "past the timeout, still waiting")
        self.assertEqual(err.getvalue().count(compat.TITLE), 1)
        self.assertNotIn("ready", out.getvalue())

    def test_no_scheduled_check_runs_on_newer_data(self):
        self.make_newer()
        with mock.patch("bluesky_monitor.poller.poll_all_active_accounts") as poll, \
                mock.patch("bluesky_monitor.locks.acquire_scheduler_lease") as lease:
            self.assertFalse(scheduler.run_cycle("T"))
        poll.assert_not_called()
        lease.assert_not_called()
        self.assertFalse(SchedulerState.objects.exists(), "nothing is written")


@override_settings(ALERTBOT_BLOCKED=compat.BLOCKED_NEWER_DATA)
class WarningPageTests(TestCase):
    """While blocked, the web service answers every request with the warning, without the database."""

    def test_pages_explain_what_happened(self):
        with self.assertNumQueries(0):
            res = self.client.get("/accounts")
        self.assertEqual(res.status_code, 503)
        body = res.content.decode()
        self.assertIn(compat.TITLE, body)
        self.assertIn("start.bat in the folder of your newest download", body)
        self.assertIn("restore.bat", body)
        self.assertIn('lang="zh-Hant-TW"', body)
        self.assertNotIn("<script", body)
        self.assertEqual(res["Content-Security-Policy"].split(";")[0], "default-src 'none'")
        self.assertEqual(res["X-Frame-Options"], "DENY")

    def test_the_health_check_says_newer_data(self):
        with self.assertNumQueries(0):
            res = self.client.get("/api/health/")
        self.assertEqual(res.status_code, 503)
        self.assertEqual((res.json()["status"], res.json()["code"]), ("newer_data", "newer_data"))

    def test_nothing_can_be_changed(self):
        with self.assertNumQueries(0):
            for method, path in [("post", "/api/accounts/"), ("patch", "/api/settings/"),
                                 ("delete", "/api/webhooks/1/"), ("post", "/api/accounts/check_all/")]:
                res = getattr(self.client, method)(path, {"handle": "x.bsky.social"}, content_type="application/json")
                self.assertEqual(res.status_code, 503, path)
        self.assertFalse(MonitoredAccount.objects.exists())
