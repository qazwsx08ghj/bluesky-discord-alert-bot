"""Step 3: settings in the database.

Covers the first-time import, repeated startups, saving, partial failures, the .env copy
kept for rollback, the management commands and secret handling. Every test that touches
a .env file uses a temporary one.
"""
import logging
import os
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.db import DatabaseError
from django.test import TestCase, override_settings
from dotenv import dotenv_values
from rest_framework.test import APITestCase

from bluesky_monitor import poller, scheduler
from bluesky_monitor.models import MonitoredAccount
from bluesky_monitor.tests import FakeSession, feed_item
from discord_alerts.sender import DeliveryResult
from . import envfile, store
from .models import BotSettings
from .secrets import is_masked, mask_webhook_url

HOOK = "https://discord.com/api/webhooks/123456789/abcdefghijklmnopqrstuvwxyz"
NEW_HOOK = "https://discord.com/api/webhooks/987654321/ZYXWVUTSRQPONMLKJIHGFEDCBA"
PASSWORD = "abcd-efgh-ijkl-mnop"
LEGACY = dict(BLUESKY_HANDLE="me.bsky.social", BLUESKY_PASSWORD=PASSWORD,
              DISCORD_WEBHOOK_URL=HOOK, POLL_INTERVAL_MINUTES="10")
SECRETS = (PASSWORD, "abcdefghijklmnopqrstuvwxyz", "ZYXWVUTSRQPONMLKJIHGFEDCBA", "s3cret pass#word")


class TempEnv:
    """A temporary .env file that the store mirrors into."""

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.env = self.dir / ".env"
        patcher = override_settings(SETTINGS_ENV_MIRROR_PATH=str(self.env))
        patcher.enable()
        self.addCleanup(patcher.disable)

    def env_values(self):
        return dotenv_values(self.env, encoding="utf-8")

    def leftover_temp_files(self):
        return [p.name for p in self.dir.iterdir() if p.name.startswith(".env.tmp-")]


class LogCapture:
    """Collects every log line from the bot's loggers, to check no secret is ever written."""

    def capture_logs(self):
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        loggers = [logging.getLogger(n) for n in ("botconfig", "bluesky_monitor", "discord_alerts")]
        old_levels = [lg.level for lg in loggers]
        for lg in loggers:
            lg.addHandler(handler)
            lg.setLevel(logging.DEBUG)

        def restore():
            for lg, level in zip(loggers, old_levels):
                lg.removeHandler(handler)
                lg.setLevel(level)
        self.addCleanup(restore)
        return stream

    def assert_no_secrets(self, text):
        for secret in SECRETS:
            self.assertNotIn(secret, text)


# -- 1. First-time import -----------------------------------------------------------
@override_settings(**LEGACY)
class FirstImportTests(TempEnv, LogCapture, TestCase):
    def test_settings_are_imported_from_env_once(self):
        logs = self.capture_logs()
        row = store.get()
        self.assertEqual((row.bluesky_handle, row.bluesky_password, row.discord_webhook_url,
                          row.poll_interval_minutes), ("me.bsky.social", PASSWORD, HOOK, 10))
        self.assertEqual(row.source, BotSettings.SOURCE_ENV)
        self.assertIsNotNone(row.imported_at)
        self.assertEqual(row.version, 1)
        self.assertIn("BLUESKY_PASSWORD=set", logs.getvalue())
        self.assert_no_secrets(logs.getvalue())

    @override_settings(BLUESKY_HANDLE="", BLUESKY_PASSWORD="", DISCORD_WEBHOOK_URL="", POLL_INTERVAL_MINUTES="5")
    def test_nothing_in_env_starts_with_empty_settings(self):
        row = store.get()
        self.assertEqual(row.source, BotSettings.SOURCE_DEFAULTS)
        self.assertEqual((row.bluesky_handle, row.poll_interval_minutes), ("", 5))

    @override_settings(BLUESKY_HANDLE="@me.bsky.social ", POLL_INTERVAL_MINUTES="120")
    def test_values_are_tidied_and_out_of_range_interval_is_clamped(self):
        row = store.get()
        self.assertEqual(row.bluesky_handle, "me.bsky.social")
        self.assertEqual(row.poll_interval_minutes, 60)

    @override_settings(POLL_INTERVAL_MINUTES="five")
    def test_unreadable_interval_uses_the_default(self):
        self.assertEqual(store.get().poll_interval_minutes, 5)

    def test_two_processes_importing_at_once_end_with_one_row(self):
        """The loser of the race gets an IntegrityError and uses the winner's row."""
        winner = store.get()
        with mock.patch.object(BotSettings.objects, "filter") as filt:
            filt.return_value.first.return_value = None  # "no row yet" as seen by the slower process
            row = store.get()
        self.assertEqual(row.pk, winner.pk)
        self.assertEqual(BotSettings.objects.count(), 1)

    def test_import_does_not_write_env_until_the_checker_starts(self):
        store.get()
        self.assertFalse(self.env.exists())


# -- 2. Repeated startups -----------------------------------------------------------
@override_settings(**LEGACY)
class RepeatedStartupTests(TempEnv, LogCapture, TestCase):
    def test_later_startups_never_overwrite_saved_settings(self):
        store.get()
        store.save({"poll_interval_minutes": 15, "bluesky_handle": "saved.bsky.social"})
        with override_settings(BLUESKY_HANDLE="other.bsky.social", POLL_INTERVAL_MINUTES="2"):
            for _ in range(3):
                store.startup_check()
        row = store.get()
        self.assertEqual((row.bluesky_handle, row.poll_interval_minutes), ("saved.bsky.social", 15))

    def test_first_start_stamps_env_and_keeps_a_copy_of_the_original(self):
        original = b"SECRET_KEY=keep-me\r\nBLUESKY_HANDLE=me.bsky.social\r\n"
        self.env.write_bytes(original)
        store.startup_check()
        self.assertEqual(self.env_values()[envfile.STAMP_KEY], "1")
        self.assertEqual((self.dir / ".env.before-db-settings").read_bytes(), original)
        self.assertIn(b"\r\n", self.env.read_bytes(), "Windows line endings are kept")
        self.assertEqual(self.env_values()["SECRET_KEY"], "keep-me")

    def test_repeated_startups_change_nothing(self):
        store.startup_check()
        first = self.env.read_bytes()
        version = store.get().version
        for _ in range(3):
            store.startup_check()
        self.assertEqual(self.env.read_bytes(), first)
        self.assertEqual(store.get().version, version)

    def test_hand_edited_env_is_reported_but_not_applied_or_overwritten(self):
        store.startup_check()
        text = self.env.read_text(encoding="utf-8").replace("POLL_INTERVAL_MINUTES=10", "POLL_INTERVAL_MINUTES=3")
        self.env.write_text(text, encoding="utf-8")
        logs = self.capture_logs()
        self.assertEqual(store.startup_check(), ["POLL_INTERVAL_MINUTES"])
        self.assertEqual(store.get().poll_interval_minutes, 10, "the database wins")
        self.assertIn("POLL_INTERVAL_MINUTES=3", self.env.read_text(encoding="utf-8"), "the edit isn't thrown away")
        self.assertIn("import_env_settings --force", logs.getvalue())

    def test_checker_start_runs_the_settings_check(self):
        with mock.patch("botconfig.store.startup_check") as check:
            scheduler._mark_started("A")
        check.assert_called_once()

    def test_a_settings_problem_does_not_stop_the_checker_starting(self):
        with mock.patch("botconfig.store.startup_check", side_effect=RuntimeError("boom")):
            scheduler._mark_started("A")  # no exception


# -- 3. Saving and applying without a restart ----------------------------------------
@override_settings(**LEGACY)
class SaveTests(TempEnv, LogCapture, TestCase):
    def test_save_bumps_version_and_updates_env(self):
        result = store.save({"poll_interval_minutes": 20})
        self.assertEqual(result.changed, ["poll_interval_minutes"])
        self.assertEqual(result.settings.version, 2)
        self.assertEqual(self.env_values()["POLL_INTERVAL_MINUTES"], "20")
        self.assertEqual(self.env_values()[envfile.STAMP_KEY], "2")
        self.assertTrue(result.settings.env_mirror_ok)

    def test_saving_the_same_values_changes_nothing(self):
        store.get()
        result = store.save({"poll_interval_minutes": 10, "bluesky_handle": "me.bsky.social"})
        self.assertEqual(result.changed, [])
        self.assertEqual(store.get().version, 1)

    def test_logs_name_changed_keys_but_never_values(self):
        logs = self.capture_logs()
        store.save({"bluesky_password": "s3cret pass#word", "discord_webhook_url": NEW_HOOK})
        self.assertIn("BLUESKY_PASSWORD", logs.getvalue())
        self.assert_no_secrets(logs.getvalue())

    def test_two_saves_at_once_keep_both_changes(self):
        """A save that read an older version retries on the new one instead of overwriting it."""
        store.get()
        real_filter = BotSettings.objects.filter
        sneaked = []

        def filter_with_a_concurrent_save(*args, **kwargs):
            if "version" in kwargs and not sneaked:
                sneaked.append(1)  # another process saves between our read and our write
                real_filter(pk=1).update(bluesky_handle="other.bsky.social", version=kwargs["version"] + 1)
            return real_filter(*args, **kwargs)

        with mock.patch.object(BotSettings.objects, "filter", side_effect=filter_with_a_concurrent_save):
            store.save({"poll_interval_minutes": 30})
        row = store.get()
        self.assertEqual((row.bluesky_handle, row.poll_interval_minutes), ("other.bsky.social", 30))
        self.assertEqual(row.version, 3)

    def test_new_webhook_is_used_by_the_next_announcement(self):
        account = MonitoredAccount.objects.create(handle="artist.bsky.social", first_poll_done=True)
        with mock.patch("discord_alerts.sender.deliver_alert", return_value=DeliveryResult(True)) as send:
            poller.poll_account(account, session=FakeSession(feed_item("a")))
            store.save({"discord_webhook_url": NEW_HOOK})
            poller.poll_account(account, session=FakeSession(feed_item("b"), feed_item("a")))
        self.assertEqual([c.kwargs["webhook_url"] for c in send.call_args_list], [HOOK, NEW_HOOK])

    def test_new_interval_is_picked_up_by_the_running_checker(self):
        sched = scheduler._build(scheduler.BackgroundScheduler, "A")
        self.assertFalse(scheduler.apply_interval(sched), "unchanged interval: nothing to do")
        store.save({"poll_interval_minutes": 2})
        self.assertTrue(scheduler.apply_interval(sched))
        from datetime import timedelta
        self.assertEqual(sched.get_job(scheduler.POLL_JOB_ID).trigger.interval, timedelta(minutes=2))


# -- 4. Partial failures ------------------------------------------------------------
@override_settings(**LEGACY)
class PartialFailureTests(TempEnv, LogCapture, APITestCase):
    url = "/api/settings/"

    def setUp(self):
        super().setUp()
        store.startup_check()  # imported and stamped, as after a normal start
        self.env_before = self.env.read_bytes()

    def test_invalid_input_changes_nothing_anywhere(self):
        for body in ({"poll_interval_minutes": 0}, {"poll_interval_minutes": 61},
                     {"poll_interval_minutes": "soon"}, {"discord_webhook_url": "not a url"},
                     {"bluesky_handle": "has spaces", "poll_interval_minutes": 20}):
            with self.subTest(body=body):
                res = self.client.post(self.url, body, format="json")
                self.assertEqual(res.status_code, 400)
                self.assertEqual(res.data["code"], "invalid_settings")
                self.assertEqual(store.get().version, 1)
                self.assertEqual(self.env.read_bytes(), self.env_before)

    def test_database_failure_leaves_env_untouched(self):
        with mock.patch.object(BotSettings.objects, "filter", side_effect=DatabaseError("disk I/O error")):
            res = self.client.post(self.url, {"poll_interval_minutes": 20}, format="json")
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.data["code"], "settings_not_saved")
        self.assertEqual(self.env.read_bytes(), self.env_before)
        self.assertEqual(store.get().poll_interval_minutes, 10)

    def test_env_write_failure_keeps_the_database_save_and_says_so(self):
        logs = self.capture_logs()
        with mock.patch("botconfig.envfile.os.replace", side_effect=PermissionError(13, "Access is denied")):
            res = self.client.post(self.url, {"poll_interval_minutes": 20, "bluesky_password": "s3cret pass#word"},
                                   format="json")
        self.assertEqual(res.status_code, 200)
        self.assertIn("warning", res.data)
        self.assertFalse(res.data["settings"]["env_file_in_sync"])
        row = store.get()
        self.assertEqual((row.poll_interval_minutes, row.version), (20, 2), "the new settings are in use")
        self.assertFalse(row.env_mirror_ok)
        self.assertEqual(self.env.read_bytes(), self.env_before, ".env is the complete old file, not half-written")
        self.assertEqual(self.leftover_temp_files(), [])
        self.assert_no_secrets(logs.getvalue() + str(res.data))

    def test_failed_env_copy_is_repaired_on_the_next_start(self):
        with mock.patch("botconfig.envfile.os.replace", side_effect=PermissionError(13, "Access is denied")):
            store.save({"poll_interval_minutes": 20})
        store.startup_check()
        self.assertEqual(self.env_values()["POLL_INTERVAL_MINUTES"], "20")
        self.assertEqual(self.env_values()[envfile.STAMP_KEY], "2")
        self.assertTrue(store.get().env_mirror_ok)

    def test_failed_env_copy_is_repaired_by_the_next_save(self):
        with mock.patch("botconfig.envfile.os.replace", side_effect=PermissionError(13, "Access is denied")):
            store.save({"poll_interval_minutes": 20})
        store.save({"bluesky_handle": "next.bsky.social"})
        values = self.env_values()
        self.assertEqual((values["POLL_INTERVAL_MINUTES"], values["BLUESKY_HANDLE"]), ("20", "next.bsky.social"))

    def test_crash_between_database_save_and_env_copy_is_repaired(self):
        """Simulates the process dying right after the database commit."""
        with mock.patch("botconfig.store.sync_env_file", return_value=(True, "")):
            store.save({"poll_interval_minutes": 25})
        self.assertEqual(self.env_values()["POLL_INTERVAL_MINUTES"], "10", ".env is behind (version 1)")
        self.assertEqual(store.startup_check(), [], "a stale copy is repaired, not reported as a hand edit")
        self.assertEqual(self.env_values()["POLL_INTERVAL_MINUTES"], "25")

    def test_a_save_landing_during_the_env_write_is_copied_too(self):
        real_write = envfile.write
        calls = []

        def write_while_another_save_lands(path, values):
            real_write(path, values)
            if not calls:
                calls.append(1)
                BotSettings.objects.filter(pk=1).update(poll_interval_minutes=45, version=store.get().version + 1)

        with mock.patch("botconfig.envfile.write", side_effect=write_while_another_save_lands):
            store.save({"poll_interval_minutes": 20})
        self.assertEqual(self.env_values()["POLL_INTERVAL_MINUTES"], "45")
        self.assertEqual(self.env_values()[envfile.STAMP_KEY], str(store.get().version))


# -- 5. Rollback compatibility (in-process; the real old code is run in tests_rollback.py)
@override_settings(**LEGACY)
class RollbackCopyTests(TempEnv, TestCase):
    def old_step2_read_env(self):
        """The control panel's .env reader from before step 3, unchanged."""
        values = {}
        for line in self.env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip()
        return values

    def test_old_code_reads_exactly_the_saved_settings(self):
        store.save({"bluesky_handle": "rolled.bsky.social", "bluesky_password": PASSWORD,
                    "discord_webhook_url": NEW_HOOK, "poll_interval_minutes": 7})
        loaded = self.env_values()  # what the old settings.py loads (python-dotenv)
        self.assertEqual(loaded["BLUESKY_HANDLE"], "rolled.bsky.social")
        self.assertEqual(loaded["BLUESKY_PASSWORD"], PASSWORD)
        self.assertEqual(loaded["DISCORD_WEBHOOK_URL"], NEW_HOOK)
        self.assertEqual(int(loaded["POLL_INTERVAL_MINUTES"]), 7)
        old_panel = self.old_step2_read_env()
        self.assertEqual(old_panel["BLUESKY_HANDLE"], "rolled.bsky.social")
        self.assertEqual(old_panel["POLL_INTERVAL_MINUTES"], "7")

    def test_unusual_passwords_survive_the_round_trip(self):
        for pw in ("s3cret pass#word", "it's", 'say "hi" #1', "dollar$sign", "back\\slash", "équipe"):
            with self.subTest(pw=pw):
                store.save({"bluesky_password": pw})
                self.assertEqual(self.env_values()["BLUESKY_PASSWORD"], pw)

    def test_each_key_appears_once_even_if_duplicated_before(self):
        self.env.write_text("BLUESKY_HANDLE=a\nBLUESKY_HANDLE=b\n# keep me\n", encoding="utf-8")
        store.save({"bluesky_handle": "c.bsky.social"})
        text = self.env.read_text(encoding="utf-8")
        self.assertEqual(text.count("BLUESKY_HANDLE="), 1)
        self.assertIn("# keep me", text)

    def test_export_command_rewrites_env_for_a_rollback(self):
        store.save({"poll_interval_minutes": 12})
        self.env.write_text("SECRET_KEY=x\n", encoding="utf-8")  # e.g. .env replaced by an old copy
        call_command("export_env_settings", stdout=StringIO())
        values = self.env_values()
        self.assertEqual((values["POLL_INTERVAL_MINUTES"], values["SECRET_KEY"]), ("12", "x"))


# -- 6. Management commands ---------------------------------------------------------
@override_settings(**LEGACY)
class CommandTests(TempEnv, LogCapture, TestCase):
    def test_import_command_does_nothing_when_settings_exist(self):
        store.save({"poll_interval_minutes": 30})
        out = StringIO()
        call_command("import_env_settings", stdout=out)
        self.assertIn("nothing was changed", out.getvalue())
        self.assertEqual(store.get().poll_interval_minutes, 30)

    def test_import_command_imports_when_empty_and_never_prints_secrets(self):
        out = StringIO()
        call_command("import_env_settings", stdout=out)
        self.assertEqual(store.get().bluesky_password, PASSWORD)
        self.assertIn("BLUESKY_PASSWORD=set", out.getvalue())
        self.assert_no_secrets(out.getvalue())

    def test_forced_import_replaces_settings_with_env_values(self):
        store.save({"poll_interval_minutes": 30, "bluesky_handle": "panel.bsky.social"})
        call_command("import_env_settings", "--force", stdout=StringIO())
        row = store.get()
        self.assertEqual((row.bluesky_handle, row.poll_interval_minutes), ("me.bsky.social", 10))
        self.assertEqual(self.env_values()[envfile.STAMP_KEY], str(row.version))

    @override_settings(BLUESKY_PASSWORD="")
    def test_forced_import_with_blank_password_keeps_the_saved_one(self):
        store.save({"bluesky_password": "kept"})
        call_command("import_env_settings", "--force", stdout=StringIO())
        self.assertEqual(store.get().bluesky_password, "kept")


class BackupCommandTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.source = self.dir / "live.sqlite3"
        import sqlite3
        con = sqlite3.connect(self.source)
        con.execute("create table t (x)")
        con.execute("insert into t values (42)")
        con.commit()
        con.close()
        db = dict(settings.DATABASES["default"], NAME=str(self.source))
        patcher = mock.patch.dict(settings.DATABASES, {"default": db})
        patcher.start()
        self.addCleanup(patcher.stop)

    def backups(self):
        return sorted((self.dir / "backups").glob("db-auto-*.sqlite3"))

    def run_backup(self, *args):
        call_command("backup_db", "--dir", str(self.dir / "backups"), *args, stdout=StringIO())

    def test_backup_is_a_consistent_copy(self):
        import sqlite3
        self.run_backup()
        (copy,) = self.backups()
        con = sqlite3.connect(copy)
        try:
            self.assertEqual(con.execute("select x from t").fetchone()[0], 42)
        finally:
            con.close()

    def test_only_when_migrations_are_pending(self):
        with mock.patch("botconfig.management.commands.backup_db.pending_migrations", return_value=[]):
            self.run_backup("--if-pending")
        self.assertEqual(self.backups(), [])
        with mock.patch("botconfig.management.commands.backup_db.pending_migrations", return_value=["x"]):
            self.run_backup("--if-pending")
        self.assertEqual(len(self.backups()), 1)

    def test_keeps_the_newest_five(self):
        for _ in range(7):
            self.run_backup()
        self.assertEqual(len(self.backups()), 5)


# -- 7. Secrets ---------------------------------------------------------------------
class SecretTests(TestCase):
    def test_webhook_masking(self):
        self.assertEqual(mask_webhook_url(HOOK), "https://discord.com/api/webhooks/123456789/****wxyz")
        self.assertEqual(mask_webhook_url("https://example.com/hook?token=abc"), "https://example.com/****")
        self.assertEqual(mask_webhook_url(""), "")
        self.assertTrue(is_masked(mask_webhook_url(HOOK)))
        self.assertFalse(is_masked(HOOK))

    @override_settings(**LEGACY)
    def test_settings_row_text_never_shows_secrets(self):
        row = store.get()
        self.assertNotIn(PASSWORD, str(row) + repr(row))

    def test_tests_never_use_the_real_env_file(self):
        self.assertEqual(settings.SETTINGS_ENV_MIRROR_PATH, "")
        self.assertIsNone(store.env_path())
        self.assertEqual(settings.BLUESKY_PASSWORD, "", "real .env values are not loaded in tests")

    @override_settings(**LEGACY)
    def test_mirror_off_means_no_env_writes(self):
        with mock.patch("botconfig.envfile.write") as write:
            store.save({"poll_interval_minutes": 20})
            store.startup_check()
        write.assert_not_called()
