"""Removing the legacy copies loses no destination or setting.

- The two migrations that drop the rollback columns keep every route, also for data that
  older code wrote after a rollback, and can be undone.
- An install like the earliest version (only Django's own tables, settings in .env) is
  migrated from scratch and its .env values are imported exactly.
- The post history is capped per account without touching posts that are still waiting.
"""
import tempfile
from pathlib import Path

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings

from bluesky_monitor import retention
from bluesky_monitor.models import MonitoredAccount, SeenPost
from botconfig import store
from discord_alerts import channels
from discord_alerts.models import DiscordWebhook

A = "https://discord.com/api/webhooks/201/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa201"
B = "https://discord.com/api/webhooks/202/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb202"
C = "https://discord.com/api/webhooks/203/cccccccccccccccccccccccccccccc203"

STEP9 = [("bluesky_monitor", "0006_check_jobs"), ("botconfig", "0001_bot_settings"),
         ("discord_alerts", "0002_channel_health_and_single_default"), ("activity", "0001_initial")]


class LegacyColumnMigrationTests(TransactionTestCase):
    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return MigrationExecutor(connection).loader.project_state(targets).apps

    def latest(self):
        executor = MigrationExecutor(connection)
        return executor.migrate(executor.loader.graph.leaf_nodes())

    def tearDown(self):
        self.latest()

    def test_routes_written_by_older_code_survive_the_column_removal(self):
        old = self.migrate(STEP9)
        Webhook = old.get_model("discord_alerts", "DiscordWebhook")
        Account = old.get_model("bluesky_monitor", "MonitoredAccount")
        Settings = old.get_model("botconfig", "BotSettings")
        art = Webhook.objects.create(name="art", url=B)
        # Normal data from the previous version: the account has a channel, and the column mirrors it.
        Account.objects.create(handle="normal.bsky.social", channel=art, discord_webhook_url=B)
        # Written by much older code after a rollback: only the URL, no channel.
        Account.objects.create(handle="old-own.bsky.social", discord_webhook_url=C)
        Account.objects.create(handle="old-shared.bsky.social", discord_webhook_url=B)
        Account.objects.create(handle="follower.bsky.social", discord_webhook_url="")
        # ... and a default URL only in the settings row, with no default channel.
        Settings.objects.create(pk=1, discord_webhook_url=A, poll_interval_minutes=7, version=4)

        self.latest()
        routes = {a.handle: channels.route_for(a).url for a in MonitoredAccount.objects.all()}
        self.assertEqual(routes, {"normal.bsky.social": B, "old-own.bsky.social": C,
                                  "old-shared.bsky.social": B, "follower.bsky.social": A})
        self.assertEqual(DiscordWebhook.objects.filter(url=B).count(), 1, "an existing channel is reused")
        self.assertEqual(channels.default_channel().url, A)
        self.assertEqual(store.get().poll_interval_minutes, 7)
        columns = [c.name for c in connection.introspection.get_table_description(
            connection.cursor(), "bluesky_monitor_monitoredaccount")]
        self.assertNotIn("discord_webhook_url", columns)

    def test_the_removal_can_be_undone(self):
        art = DiscordWebhook.objects.create(name="art", url=B)
        channels.make_default(DiscordWebhook.objects.create(name="main", url=A))
        MonitoredAccount.objects.create(handle="own.bsky.social", channel=art)
        MonitoredAccount.objects.create(handle="follower.bsky.social")
        store.save({"poll_interval_minutes": 9})

        old = self.migrate(STEP9)
        Account = old.get_model("bluesky_monitor", "MonitoredAccount")
        Settings = old.get_model("botconfig", "BotSettings")
        self.assertEqual(dict(Account.objects.values_list("handle", "discord_webhook_url")),
                         {"own.bsky.social": B, "follower.bsky.social": ""})
        self.assertEqual(Settings.objects.get(pk=1).discord_webhook_url, A)
        self.latest()
        self.assertEqual(channels.route_for(MonitoredAccount.objects.get(handle="own.bsky.social")).url, B)


class EarliestInstallTests(TransactionTestCase):
    """The shape of a first-version install: Django's tables only; settings in .env."""

    def test_migrated_from_scratch_and_the_env_values_imported_exactly(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("bluesky_monitor", None), ("discord_alerts", None), ("botconfig", None),
                          ("activity", None)])
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text(f"SECRET_KEY=x\nDEBUG=True\nBLUESKY_HANDLE=me.bsky.social\n"
                           f"BLUESKY_PASSWORD=abcd-efgh-ijkl-mnop\nDISCORD_WEBHOOK_URL={A}\n"
                           "POLL_INTERVAL_MINUTES=7\n", encoding="utf-8")
            from dotenv import dotenv_values
            values = dotenv_values(env)
            with override_settings(**{k: values[k] for k in ("BLUESKY_HANDLE", "BLUESKY_PASSWORD",
                                                                 "DISCORD_WEBHOOK_URL", "POLL_INTERVAL_MINUTES")}):
                MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
                row = store.get()
            self.assertEqual(env.read_text(encoding="utf-8").count("\n"), 6, "the old .env is only read")
        self.assertEqual((row.bluesky_handle, row.bluesky_password, row.poll_interval_minutes),
                         ("me.bsky.social", "abcd-efgh-ijkl-mnop", 7))
        self.assertEqual(list(DiscordWebhook.objects.values_list("url", "is_default")), [(A, True)])
        self.assertEqual(MonitoredAccount.objects.count(), 0)


class PostHistoryTests(TestCase):
    def posts(self, account, n, status=SeenPost.SENT, start=0):
        for i in range(start, start + n):
            SeenPost.objects.create(account=account, post_uri=f"at://{account.handle}/post/{i}", post_cid=str(i),
                                    status=status)

    def test_each_account_keeps_its_newest_records(self):
        busy = MonitoredAccount.objects.create(handle="busy.bsky.social")
        quiet = MonitoredAccount.objects.create(handle="quiet.bsky.social")
        self.posts(busy, 30)
        self.posts(quiet, 5)
        self.assertEqual(retention.prune_seen_posts(keep=10), 20)
        kept = list(SeenPost.objects.filter(account=busy).order_by("id").values_list("post_uri", flat=True))
        self.assertEqual(kept, [f"at://busy.bsky.social/post/{i}" for i in range(20, 30)])
        self.assertEqual(SeenPost.objects.filter(account=quiet).count(), 5)

    def test_posts_still_to_be_sent_are_never_deleted(self):
        acc = MonitoredAccount.objects.create(handle="a.bsky.social")
        self.posts(acc, 3, status=SeenPost.RETRY)
        self.posts(acc, 3, status=SeenPost.PENDING, start=3)
        self.posts(acc, 10, start=6)
        retention.prune_seen_posts(keep=4)
        self.assertEqual(SeenPost.objects.filter(status__in=[SeenPost.RETRY, SeenPost.PENDING]).count(), 6)
        self.assertEqual(SeenPost.objects.filter(status=SeenPost.SENT).count(), 4)

    def test_a_pruned_account_still_announces_nothing_old(self):
        """The newest records are what a check compares against, so nothing old comes back."""
        from unittest import mock

        from bluesky_monitor import poller
        from bluesky_monitor.tests import FakeSession, feed_item
        from discord_alerts.sender import DeliveryResult

        channels.make_default(DiscordWebhook.objects.create(name="main", url=A))
        acc = MonitoredAccount.objects.create(handle="artist.bsky.social", first_poll_done=True)
        items = [feed_item(str(i)) for i in range(40, 0, -1)]  # newest first, like a feed
        with mock.patch("discord_alerts.sender.deliver_alert", return_value=DeliveryResult(True)) as send:
            poller.poll_account(acc, session=FakeSession(*items))
            sent_before = send.call_count
            retention.prune_seen_posts(keep=35)
            poller.poll_account(acc, session=FakeSession(*items[:30]))
        self.assertEqual(send.call_count, sent_before, "no post is announced twice")


class RestoreCommandTests(TestCase):
    """manage.py restore_db, used by restore.bat (with the bot stopped)."""

    def setUp(self):
        import sqlite3

        from django.conf import settings
        from unittest import mock

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.live = self.dir / "db.sqlite3"
        self.make_db(self.live, "current")
        db = dict(settings.DATABASES["default"], NAME=str(self.live))
        patcher = mock.patch.dict(settings.DATABASES, {"default": db})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sqlite3 = sqlite3

    def make_db(self, path, value, bot=True):
        import sqlite3

        con = sqlite3.connect(path)
        if bot:
            con.execute("create table django_migrations (id integer primary key, app text, name text)")
        con.execute("create table marker (v text)")
        con.execute("insert into marker values (?)", (value,))
        con.commit()
        con.close()

    def marker(self):
        con = self.sqlite3.connect(self.live)
        try:
            return con.execute("select v from marker").fetchone()[0]
        finally:
            con.close()

    def restore(self, path):
        from django.core.management import call_command
        from io import StringIO

        out = StringIO()
        call_command("restore_db", str(path), stdout=out)
        return out.getvalue()

    def test_a_backup_is_put_back_and_old_journal_files_are_removed(self):
        backup = self.dir / "db-manual-1.sqlite3"
        self.make_db(backup, "from the backup")
        Path(f"{self.live}-wal").write_bytes(b"journal of the replaced database")
        self.assertIn("Restored db-manual-1.sqlite3", self.restore(backup))
        self.assertEqual(self.marker(), "from the backup")
        self.assertFalse(Path(f"{self.live}-wal").exists())
        self.assertTrue(backup.exists(), "the backup itself is kept")

    def test_a_damaged_file_is_refused_and_nothing_changes(self):
        from django.core.management.base import CommandError

        broken = self.dir / "db-manual-2.sqlite3"
        broken.write_bytes(b"this is not a database" * 100)
        with self.assertRaisesMessage(CommandError, "damaged"):
            self.restore(broken)
        self.assertEqual(self.marker(), "current")

    def test_a_database_of_something_else_is_refused(self):
        from django.core.management.base import CommandError

        other = self.dir / "db-other.sqlite3"
        self.make_db(other, "not the bot", bot=False)
        with self.assertRaisesMessage(CommandError, "isn't a backup of this bot"):
            self.restore(other)
        self.assertEqual(self.marker(), "current")

    def test_a_missing_or_wrong_file_is_refused(self):
        from django.core.management.base import CommandError

        for path in (self.dir / "missing.sqlite3", self.dir / "notes.txt"):
            with self.subTest(path=path.name), self.assertRaisesMessage(CommandError, "There is no backup"):
                self.restore(path)
