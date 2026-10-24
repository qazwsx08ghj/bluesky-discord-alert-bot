"""The updater's steps inside the image: update_plan, update_preflight, update_selftest, data_fingerprint."""
import json
import shutil
import sqlite3
from contextlib import closing
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase

from release_meta import IMAGE, MANIFEST_ASSET, REPOSITORY, github
from release_meta.tests import make_manifest
from release_meta.tests_github import FakeGitHub
from .tests_step8 import DataFolderTestCase, sha


class UpdatePlanTests(SimpleTestCase):
    def setUp(self):
        self.gh = FakeGitHub()
        self.addCleanup(self.gh.close)
        self.gh.json(f"/repos/{REPOSITORY}/contents/release/withdrawn.json?ref=main",
                     {"schema": "alertbot-withdrawn/1", "revision": 4, "updated_at": "2026-12-01", "withdrawn": []})
        self.releases = []

    def publish(self, version, **changes):
        self.gh.json(f"/dl/{version}", make_manifest(version, **changes))
        self.releases.append({"tag_name": f"v{version}", "draft": False, "immutable": True, "prerelease": "-" in version,
                              "assets": [{"name": MANIFEST_ASSET, "size": 900, "digest": None,
                                          "browser_download_url": f"{self.gh.base}/dl/{version}"},
                                         {"name": f"BlueskyDiscordAlertBot-{version}.zip", "size": 4096, "digest": None,
                                          "browser_download_url": f"{self.gh.base}/dl/zip"}]})
        self.gh.json(f"/repos/{REPOSITORY}/releases?per_page=100", self.releases)

    def plan(self, installed="1.0.0-rc.3", *args, test_build=True):
        out = StringIO()
        with mock.patch.dict("os.environ", {"ALERTBOT_UPDATE_TEST_API": self.gh.base}), \
                mock.patch("botconfig.management.commands.update_plan.is_test_build", return_value=test_build), \
                mock.patch("release_meta.is_test_build", return_value=test_build), \
                self.settings(APP_VERSION=installed):
            call_command("update_plan", "--channel", "prerelease", *args, stdout=out)
        return dict(line.split("=", 1) for line in out.getvalue().splitlines())

    def test_an_update(self):
        self.publish("1.0.0-rc.4")
        plan = self.plan()
        self.assertEqual(plan["RESULT"], "update")
        self.assertEqual(plan["TARGET_VERSION"], "1.0.0-rc.4")
        self.assertEqual(plan["TARGET_IMAGE"], f"{IMAGE}:1.0.0-rc.4@sha256:" + "1" * 64, "pinned by digest")
        self.assertEqual((plan["STEPS"], plan["SEEN_REVISION"]), ("1", "4"))
        self.assertEqual(plan["CURRENT_VERSION"], "1.0.0-rc.3")

    def test_up_to_date_and_blocked(self):
        self.publish("1.0.0-rc.3")
        self.assertEqual(self.plan()["RESULT"], "up_to_date")
        plan = self.plan("dev")
        self.assertEqual((plan["RESULT"], plan["REASON"]), ("blocked", "installed_unknown"))

    def test_a_stale_remembered_list_blocks(self):
        self.publish("1.0.0-rc.4")
        plan = self.plan("1.0.0-rc.3", "--seen-revision", "9", "--seen-digest", "f" * 64)
        self.assertEqual((plan["RESULT"], plan["REASON"]), ("blocked", "withdrawn_stale"))
        self.assertNotIn("SEEN_REVISION", plan, "nothing to remember from a refused list")
        plan = self.plan("1.0.0-rc.3", "--seen-revision", "4", "--seen-digest", "bad")
        self.assertEqual((plan["RESULT"], plan["REASON"]), ("blocked", "setup"))

    def test_messages_are_safe_for_a_batch_file(self):
        from botconfig.management.commands.update_plan import one_line

        self.assertEqual(one_line('a & b | c > d < e ^ f % g ! h "i"\nj'), "a   b   c   d   e   f   g   h  i  j")

    def test_a_fake_github_only_in_images_built_for_the_tests(self):
        made = []
        real_client = github.Client

        def record(*args, **kwargs):
            made.append(kwargs)
            raise ValueError("stop here")

        with mock.patch.object(github, "Client", side_effect=record):
            plan = self.plan(test_build=False)
        self.assertEqual(made, [{}], "the real GitHub: ALERTBOT_UPDATE_TEST_API is ignored")
        self.assertEqual(plan["RESULT"], "blocked")
        with self.assertRaises(ValueError):
            with mock.patch("release_meta.is_test_build", return_value=False):
                real_client(api="http://fakegh:8080", test_hosts=("http://fakegh:8080/",))


class UpdateStepTests(DataFolderTestCase):
    def setUp(self):
        super().setUp()
        self.manage("prepare_data")

    def test_preflight_passes_and_changes_nothing(self):
        before = sha(self.db)
        out = self.manage("update_preflight").stdout
        self.assertIn("Preflight passed", out)
        self.assertEqual(sha(self.db), before)
        self.assertEqual(sorted(p.name for p in self.data.iterdir() if "preflight" in p.name), [])

    def test_preflight_refuses_newer_data(self):
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("INSERT INTO django_migrations (app, name, applied) VALUES ('bluesky_monitor', '9999_future', '2027-01-01')")
            con.commit()
        res = self.manage("update_preflight", ok=False)
        self.assertEqual(res.returncode, 3, res.stderr)
        self.assertIn("comes from a newer version", res.stderr)

    def test_preflight_catches_a_migration_that_fails_and_changes_nothing(self):
        self.manage("migrate", "bluesky_monitor", "0005")  # an older install, with updates waiting
        with closing(sqlite3.connect(self.db)) as con:  # in the way of 0006, which creates this table
            con.execute("CREATE TABLE bluesky_monitor_checkjob (x)")
            con.commit()
        before = sha(self.db)
        res = self.manage("update_preflight", ok=False)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("Updating a copy of the data failed", res.stderr)
        self.assertEqual(sha(self.db), before, "only the copy was migrated")
        self.assertEqual(sorted(p.name for p in self.data.iterdir() if "preflight" in p.name), [], "the copy is removed")

    def test_selftest(self):
        out = self.manage("update_selftest", env={"ALERTBOT_VERSION": "1.2.3"}).stdout
        self.assertIn("Self-test passed: version 1.2.3", out)

    def test_fingerprint_ignores_bookkeeping_but_not_setup(self):
        first = self.manage("data_fingerprint").stdout.strip()
        self.assertRegex(first, r"^FINGERPRINT=[0-9a-f]{20}$")
        self.shell("from django.utils import timezone; from bluesky_monitor.models import SchedulerState; "
                   "SchedulerState.objects.update_or_create(pk=1, defaults={'heartbeat_at': timezone.now()})")
        self.assertEqual(self.manage("data_fingerprint").stdout.strip(), first)
        self.shell("from bluesky_monitor.models import MonitoredAccount; MonitoredAccount.objects.create(handle='x.bsky.social')")
        self.assertNotEqual(self.manage("data_fingerprint").stdout.strip(), first)
        self.assertEqual(sorted(p.name for p in self.data.iterdir() if "fingerprint" in p.name), [])
