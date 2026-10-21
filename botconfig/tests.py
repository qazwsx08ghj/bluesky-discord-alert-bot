"""Settings in the database: the one-time import from an earlier install's .env, saving,
the management commands, backups and secret handling.

Since step 10 nothing writes .env any more (it was copied there for rollbacks before).
"""
import logging
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.db import DatabaseError
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from bluesky_monitor import poller, scheduler
from bluesky_monitor.models import MonitoredAccount
from bluesky_monitor.tests import FakeSession, feed_item
from discord_alerts import channels
from discord_alerts.models import DiscordWebhook
from discord_alerts.sender import DeliveryResult
from . import store
from .models import BotSettings
from .secrets import is_masked, mask_webhook_url

HOOK = "https://discord.com/api/webhooks/123456789/abcdefghijklmnopqrstuvwxyz"
NEW_HOOK = "https://discord.com/api/webhooks/987654321/ZYXWVUTSRQPONMLKJIHGFEDCBA"
PASSWORD = "abcd-efgh-ijkl-mnop"
LEGACY = dict(BLUESKY_HANDLE="me.bsky.social", BLUESKY_PASSWORD=PASSWORD,
              DISCORD_WEBHOOK_URL=HOOK, POLL_INTERVAL_MINUTES="10")
SECRETS = (PASSWORD, "abcdefghijklmnopqrstuvwxyz", "ZYXWVUTSRQPONMLKJIHGFEDCBA", "s3cret pass#word")


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
class FirstImportTests(LogCapture, TestCase):
    def test_settings_are_imported_from_env_once(self):
        logs = self.capture_logs()
        row = store.get()
        self.assertEqual((row.bluesky_handle, row.bluesky_password, row.poll_interval_minutes),
                         ("me.bsky.social", PASSWORD, 10))
        self.assertEqual(row.source, BotSettings.SOURCE_ENV)
        self.assertIsNotNone(row.imported_at)
        self.assertEqual(row.version, 1)
        self.assertIn("BLUESKY_PASSWORD=set", logs.getvalue())
        self.assertIn("DISCORD_WEBHOOK_URL=set", logs.getvalue())
        self.assert_no_secrets(logs.getvalue())

    def test_the_env_webhook_becomes_the_default_channel(self):
        store.get()
        default = channels.default_channel()
        self.assertEqual((default.url, default.name), (HOOK, "Default channel"))
        self.assertEqual(store.default_webhook_url(), HOOK)

    @override_settings(BLUESKY_HANDLE="", BLUESKY_PASSWORD="", DISCORD_WEBHOOK_URL="", POLL_INTERVAL_MINUTES="5")
    def test_nothing_in_env_starts_with_empty_settings(self):
        row = store.get()
        self.assertEqual(row.source, BotSettings.SOURCE_DEFAULTS)
        self.assertEqual((row.bluesky_handle, row.poll_interval_minutes), ("", 5))
        self.assertIsNone(channels.default_channel())

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

    def test_later_startups_never_overwrite_saved_settings(self):
        store.get()
        store.save({"poll_interval_minutes": 15, "bluesky_handle": "saved.bsky.social"})
        with override_settings(BLUESKY_HANDLE="other.bsky.social", POLL_INTERVAL_MINUTES="2",
                               DISCORD_WEBHOOK_URL=NEW_HOOK):
            for _ in range(3):
                store.startup_check()
        row = store.get()
        self.assertEqual((row.bluesky_handle, row.poll_interval_minutes), ("saved.bsky.social", 15))
        self.assertEqual(channels.default_channel().url, HOOK)


# -- 2. Saving ----------------------------------------------------------------------
@override_settings(**LEGACY)
class SaveTests(LogCapture, TestCase):
    def test_save_bumps_the_version(self):
        result = store.save({"poll_interval_minutes": 20})
        self.assertEqual(result.changed, ["poll_interval_minutes"])
        self.assertEqual(result.settings.version, 2)

    def test_saving_the_same_values_changes_nothing(self):
        store.get()
        result = store.save({"poll_interval_minutes": 10, "bluesky_handle": "me.bsky.social"})
        self.assertEqual(result.changed, [])
        self.assertEqual(store.get().version, 1)

    def test_logs_name_changed_keys_but_never_values(self):
        logs = self.capture_logs()
        store.save({"bluesky_password": "s3cret pass#word"})
        self.assertIn("BLUESKY_PASSWORD", logs.getvalue())
        self.assert_no_secrets(logs.getvalue())

    def test_saving_never_writes_any_file(self):
        """Step 10: no .env copy. Saving and starting touch only the database."""
        store.get()
        with mock.patch("builtins.open", side_effect=AssertionError("a file was opened")), \
                mock.patch("pathlib.Path.write_text", side_effect=AssertionError("a file was written")), \
                mock.patch("pathlib.Path.write_bytes", side_effect=AssertionError("a file was written")):
            store.save({"poll_interval_minutes": 20, "bluesky_password": "new-pass-word-1234"})
            store.startup_check()
        self.assertEqual(store.get().poll_interval_minutes, 20)

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

    def test_a_new_default_channel_is_used_by_the_next_announcement(self):
        account = MonitoredAccount.objects.create(handle="artist.bsky.social", first_poll_done=True)
        store.get()
        with mock.patch("discord_alerts.sender.deliver_alert", return_value=DeliveryResult(True)) as send:
            poller.poll_account(account, session=FakeSession(feed_item("a")))
            channels.make_default(DiscordWebhook.objects.create(name="new", url=NEW_HOOK))
            poller.poll_account(account, session=FakeSession(feed_item("b"), feed_item("a")))
        self.assertEqual([c.kwargs["webhook_url"] for c in send.call_args_list], [HOOK, NEW_HOOK])

    def test_new_interval_is_picked_up_by_the_running_checker(self):
        sched = scheduler._build(scheduler.BackgroundScheduler, "A")
        self.assertFalse(scheduler.apply_interval(sched), "unchanged interval: nothing to do")
        store.save({"poll_interval_minutes": 2})
        self.assertTrue(scheduler.apply_interval(sched))
        from datetime import timedelta
        self.assertEqual(sched.get_job(scheduler.POLL_JOB_ID).trigger.interval, timedelta(minutes=2))


# -- 3. The settings API: invalid input and failures --------------------------------
@override_settings(**LEGACY)
class SettingsApiFailureTests(LogCapture, APITestCase):
    url = "/api/settings/"

    def setUp(self):
        super().setUp()
        store.startup_check()

    def test_invalid_input_changes_nothing(self):
        for body in ({"poll_interval_minutes": 0}, {"poll_interval_minutes": 61},
                     {"poll_interval_minutes": "soon"}, {"bluesky_handle": "has spaces", "poll_interval_minutes": 20},
                     {"discord_webhook_url": NEW_HOOK, "poll_interval_minutes": 20}):
            with self.subTest(body=body):
                res = self.client.post(self.url, body, format="json")
                self.assertEqual(res.status_code, 400)
                self.assertEqual(res.data["code"], "invalid_settings")
                self.assertEqual(store.get().version, 1)
                self.assertNotIn("abcdefghijklmnopqrstuvwxyz", str(res.data))
                self.assertNotIn("ZYXWVUTSRQPONMLKJIHGFEDCBA", str(res.data))

    def test_the_old_webhook_field_points_to_the_channels(self):
        res = self.client.patch(self.url, {"discord_webhook_url": NEW_HOOK}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("/api/webhooks/", res.data["fields"]["discord_webhook_url"][0])
        self.assertEqual(channels.default_channel().url, HOOK)

    def test_database_failure_is_reported_and_changes_nothing(self):
        with mock.patch.object(BotSettings.objects, "filter", side_effect=DatabaseError("disk I/O error")):
            res = self.client.post(self.url, {"poll_interval_minutes": 20}, format="json")
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.data["code"], "settings_not_saved")
        self.assertEqual(store.get().poll_interval_minutes, 10)


# -- 4. Management commands ---------------------------------------------------------
@override_settings(**LEGACY)
class CommandTests(LogCapture, TestCase):
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

    def test_forced_import_replaces_settings_and_the_default_channel(self):
        store.save({"poll_interval_minutes": 30, "bluesky_handle": "panel.bsky.social"})
        channels.make_default(DiscordWebhook.objects.create(name="panel", url=NEW_HOOK))
        call_command("import_env_settings", "--force", stdout=StringIO())
        row = store.get()
        self.assertEqual((row.bluesky_handle, row.poll_interval_minutes), ("me.bsky.social", 10))
        self.assertEqual(channels.default_channel().url, HOOK)

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


# -- 5. Secrets ---------------------------------------------------------------------
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
        self.assertEqual(settings.BLUESKY_PASSWORD, "", "real .env values are not loaded in tests")
        self.assertEqual(settings.DISCORD_WEBHOOK_URL, "")
        self.assertFalse(hasattr(settings, "SETTINGS_ENV_MIRROR_PATH"), "the .env copy is gone")
