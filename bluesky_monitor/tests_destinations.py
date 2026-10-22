"""Several Discord channels per account, each with an optional role to mention.

Covers: one delivery per post per channel; independent retries per channel; the role mention
and its safety (allowed_mentions, @everyone); Role ID checks in the API; never resending old
posts when channels, roles or the default change; deleting or moving a channel without
duplicates; the status page and clean-up; and the database upgrade, both ways.

Discord and Bluesky are always mocked; nothing here reaches the network.
"""
from datetime import datetime, timedelta, timezone as dt_tz
from unittest import mock

import requests
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APITestCase

from activity import retention as activity_retention, status as bot_status
from activity.models import ActivityEvent
from discord_alerts import channels, mentions, sender
from discord_alerts.models import DiscordWebhook
from discord_alerts.sender import DeliveryResult, WebhookInfo
from . import poller, retention
from .models import AccountDestination, MonitoredAccount, PostDelivery, SeenPost
from .tests import FakeSession, channel_ids, feed_item

T0 = datetime(2026, 10, 21, 12, 0, tzinfo=dt_tz.utc)
GUILD = "1100000000000000000"   # the made-up Discord server; also its @everyone role's ID
ROLE = "1189000000000000123"    # a made-up role in it
ROLE_2 = "1189000000000000456"


def hook(n):
    return f"https://discord.com/api/webhooks/{n}/token-{n}"


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


class FakeDiscord:
    """Records every send by channel token; fails a channel while it is in `down` (or with a given result)."""

    def __init__(self):
        self.sent, self.down, self.lookups = [], {}, []

    def deliver(self, webhook_url, handle, display_name, text, post_url, kind="post", role_id=""):
        token = webhook_url.rsplit("/", 1)[1]
        if token in self.down:
            return self.down[token]
        self.sent.append((token, post_url.rsplit("/", 1)[1], role_id))
        return DeliveryResult(True, status_code=200)

    def lookup(self, webhook_url, label=""):
        self.lookups.append(webhook_url.rsplit("/", 1)[1])
        return WebhookInfo(True, GUILD)

    def to(self, token):
        return [(post, role) for t, post, role in self.sent if t == token]


@override_settings(DISCORD_WEBHOOK_URL="", SEND_MAX_ATTEMPTS=4, SEND_RETRY_WINDOW_HOURS=24, SEND_STALE_SECONDS=120)
class DestinationTestCase(TestCase):
    """An account sending to #one and #two; #main is the default channel."""

    def setUp(self):
        self.clock = Clock()
        for target in ("django.utils.timezone.now",):
            p = mock.patch(target, self.clock)
            p.start()
            self.addCleanup(p.stop)
        self.discord = FakeDiscord()
        for name, fake in (("deliver_alert", self.discord.deliver), ("lookup_webhook", self.discord.lookup)):
            p = mock.patch(f"discord_alerts.sender.{name}", side_effect=fake)
            p.start()
            self.addCleanup(p.stop)
        self.main = channels.make_default(DiscordWebhook.objects.create(name="#main", url=hook(1)))
        self.one = DiscordWebhook.objects.create(name="#one", url=hook(2))
        self.two = DiscordWebhook.objects.create(name="#two", url=hook(3))
        self.account = MonitoredAccount.objects.create(handle="artist.bsky.social", first_poll_done=True)
        self.send_to(self.account, (self.one, ""), (self.two, ""))

    def send_to(self, account, *pairs):
        channels.set_destinations(account, [{"channel": c, "role_id": r} for c, r in pairs])

    def check(self, *rkeys, account=None):
        """A check that finds these posts (newest first, as Bluesky lists them)."""
        return poller.poll_account(account or self.account, session=FakeSession(*[feed_item(r) for r in rkeys]))

    def delivery(self, rkey, channel):
        return PostDelivery.objects.get(post__post_uri__endswith=f"/{rkey}", channel=channel)

    def post(self, rkey):
        return SeenPost.objects.get(post_uri__endswith=f"/{rkey}")


# -- sending to several channels ---------------------------------------------------------------
class MultiChannelDeliveryTests(DestinationTestCase):
    def test_each_channel_gets_each_new_post_once(self):
        self.check("a")
        self.assertEqual(self.discord.to("token-2"), [("a", "")])
        self.assertEqual(self.discord.to("token-3"), [("a", "")])
        self.assertEqual(self.post("a").status, SeenPost.SENT)
        self.check("b", "a")
        self.check("b", "a")
        self.assertEqual(len(self.discord.sent), 4, "a and b, once per channel, never again")

    def test_one_delivery_row_per_channel(self):
        self.check("a")
        self.assertEqual(sorted(PostDelivery.objects.values_list("channel__name", flat=True)), ["#one", "#two"])

    def test_accounts_can_share_a_channel(self):
        other = MonitoredAccount.objects.create(handle="fan.bsky.social", first_poll_done=True)
        self.send_to(other, (self.one, ""))
        self.check("a")
        self.check("z", account=other)
        self.assertEqual(self.discord.to("token-2"), [("a", ""), ("z", "")])

    def test_the_same_post_from_two_accounts_is_sent_for_each(self):
        """A repost of one post by two watched accounts: one notification per account (as before)."""
        other = MonitoredAccount.objects.create(handle="fan.bsky.social", first_poll_done=True)
        self.send_to(other, (self.one, ""))
        self.check("a")
        self.check("a", account=other)
        self.assertEqual(self.discord.to("token-2"), [("a", ""), ("a", "")])

    def test_the_default_is_sent_once_even_when_also_chosen_directly(self):
        """The account follows the default and also has #one; #one becomes the default."""
        self.send_to(self.account, (None, ""), (self.one, ROLE))
        channels.make_default(self.one)
        self.check("a")
        self.assertEqual(self.discord.to("token-2"), [("a", ROLE)], "once, with the account's own role")
        self.assertEqual(PostDelivery.objects.count(), 1)

    def test_nowhere_to_send_records_the_post_as_skipped(self):
        channels.clear_default()
        self.send_to(self.account, (None, ""))
        self.check("a")
        self.assertEqual(self.post("a").status, SeenPost.SKIPPED)
        self.assertFalse(PostDelivery.objects.exists())
        self.assertEqual(self.discord.sent, [])
        self.assertTrue(ActivityEvent.objects.filter(type="post_not_sent", category="no_channel").exists())

    def test_check_reports_posts_not_notifications(self):
        self.check("a")
        self.assertEqual(self.account.check_report.posts_sent, 1, "one post, sent to two channels")


# -- independent retries -----------------------------------------------------------------------
class PerChannelRetryTests(DestinationTestCase):
    def test_a_failing_channel_never_holds_up_or_repeats_another(self):
        self.discord.down["token-3"] = DeliveryResult(False, "Discord returned an error (500).", status_code=500,
                                                      category="discord_unavailable")
        self.check("a")
        self.assertEqual(self.delivery("a", self.one).status, PostDelivery.SENT)
        retry = self.delivery("a", self.two)
        self.assertEqual((retry.status, retry.attempts), (PostDelivery.RETRY, 1))
        self.assertEqual(retry.next_attempt_at, T0 + timedelta(minutes=1))
        self.assertEqual(self.post("a").status, SeenPost.PENDING)

        del self.discord.down["token-3"]
        self.clock.advance(minutes=2)
        self.check("a")
        self.assertEqual(self.discord.to("token-2"), [("a", "")], "#one is never sent again")
        self.assertEqual(self.discord.to("token-3"), [("a", "")])
        self.assertEqual(self.post("a").status, SeenPost.SENT)

    def test_a_failing_channel_keeps_its_own_order_while_the_others_go_on(self):
        self.discord.down["token-3"] = DeliveryResult(False, "down", category="discord_unavailable")
        self.check("c", "b", "a")
        self.assertEqual([p for p, _ in self.discord.to("token-2")], ["a", "b", "c"])
        self.assertEqual([self.delivery(r, self.two).status for r in "abc"],
                         [PostDelivery.RETRY, PostDelivery.PENDING, PostDelivery.PENDING], "b and c wait behind a")
        del self.discord.down["token-3"]
        self.clock.advance(minutes=2)
        self.check("c", "b", "a")
        self.assertEqual([p for p, _ in self.discord.to("token-3")], ["a", "b", "c"], "order is kept")

    def test_giving_up_on_one_channel_leaves_the_post_partly_sent(self):
        self.discord.down["token-3"] = DeliveryResult(False, "Discord didn't accept the webhook.", status_code=404,
                                                      category="discord_webhook_invalid")
        self.check("a")
        for minutes in (1, 5, 30):
            self.clock.advance(minutes=minutes)
            self.check("a")
        failed = self.delivery("a", self.two)
        self.assertEqual((failed.status, failed.attempts), (PostDelivery.FAILED, 4))
        self.assertEqual(self.post("a").status, SeenPost.PARTLY_SENT)
        gave_up = ActivityEvent.objects.filter(type="send_gave_up")
        self.assertEqual([e.channel_label for e in gave_up], ["#two"])

    def test_discords_wait_applies_to_that_channel_only(self):
        self.discord.down["token-3"] = DeliveryResult(False, "slow down", retry_after=600, status_code=429,
                                                      category="discord_rate_limited")
        self.check("a")
        self.assertEqual(self.delivery("a", self.two).next_attempt_at, T0 + timedelta(seconds=600))
        self.assertEqual(self.delivery("a", self.one).status, PostDelivery.SENT)

    def test_the_24_hour_limit_is_per_channel(self):
        self.discord.down["token-3"] = DeliveryResult(False, "down", category="discord_unavailable")
        self.check("a")
        self.clock.advance(hours=25)
        self.check("a")
        self.assertEqual(self.delivery("a", self.two).status, PostDelivery.FAILED)
        self.assertEqual(self.post("a").status, SeenPost.PARTLY_SENT)
        self.assertEqual(ActivityEvent.objects.get(type="send_gave_up").category, "send_expired")

    def test_an_interrupted_send_is_recovered_for_that_channel_only(self):
        poller.claim_post(self.account, feed_item("a"), SeenPost.PENDING)
        PostDelivery.objects.filter(channel=self.two).update(status=PostDelivery.SENDING, claim_token="dead",
                                                             claimed_at=T0, attempts=1)
        poller.deliver_pending(self.account)
        self.assertEqual(self.discord.to("token-3"), [], "not before the stale timeout")
        self.clock.advance(seconds=121)
        poller.deliver_pending(self.account)
        self.assertEqual(self.discord.to("token-2"), [("a", "")])
        self.assertEqual(self.discord.to("token-3"), [("a", "")])
        self.assertEqual(self.delivery("a", self.two).attempts, 2)


# -- role mentions -----------------------------------------------------------------------------
class MentionMessageTests(TestCase):
    """What Discord receives (sender.build_payload) with and without a role."""

    def payload(self, role="", text="hello"):
        return sender.build_payload("a.bsky.social", "A", text, "https://bsky.app/profile/a/post/1", "post", role)

    def test_without_a_role_nothing_can_ping(self):
        body = self.payload()
        self.assertNotIn("content", body)
        self.assertEqual(body["allowed_mentions"], {"parse": []})

    def test_a_role_is_mentioned_and_is_the_only_thing_allowed_to_ping(self):
        body = self.payload(ROLE)
        self.assertEqual(body["content"], f"<@&{ROLE}>")
        self.assertEqual(body["allowed_mentions"], {"parse": [], "roles": [ROLE]})

    def test_mentions_in_the_post_text_stay_in_the_embed(self):
        text = f"@everyone @here <@&{ROLE_2}> <@123456789012345678> look"
        for role in ("", ROLE):
            with self.subTest(role=role):
                body = self.payload(role, text=text)
                self.assertEqual(body["embeds"][0]["description"], text)
                self.assertEqual(body.get("content", ""), f"<@&{role}>" if role else "")
                self.assertEqual(body["allowed_mentions"]["parse"], [])
                self.assertNotIn("users", body["allowed_mentions"])
                self.assertEqual(body["allowed_mentions"].get("roles", []), [role] if role else [])

    def test_a_bad_stored_role_is_never_sent(self):
        with self.assertRaises(mentions.InvalidRoleId):
            self.payload("everyone")

    def test_the_payload_is_what_is_posted(self):
        with mock.patch("discord_alerts.sender.requests.post") as post:
            post.return_value.status_code = 200
            sender.deliver_alert(hook(2), "a.bsky.social", "A", "hi", "https://bsky.app/x", role_id=ROLE)
        sent = post.call_args.kwargs["json"]
        self.assertEqual((sent["content"], sent["allowed_mentions"]), (f"<@&{ROLE}>", {"parse": [], "roles": [ROLE]}))


class WebhookLookupTests(TestCase):
    """sender.lookup_webhook: which server a webhook posts to (a GET; nothing is posted)."""

    def test_the_server_id_is_read_from_discords_answer(self):
        with mock.patch("discord_alerts.sender.requests.get") as get:
            get.return_value.status_code = 200
            get.return_value.json.return_value = {"id": "9", "type": 1, "guild_id": GUILD, "channel_id": "5"}
            info = sender.lookup_webhook(hook(2))
        self.assertEqual((info.ok, info.guild_id), (True, GUILD))
        get.assert_called_once()
        self.assertNotIn("json", get.call_args.kwargs, "nothing is sent")

    def test_a_deleted_webhook_is_reported_like_a_failed_send(self):
        error = requests.HTTPError(f"404 for url: {hook(2)}", response=mock.Mock(status_code=404, text="Unknown Webhook"))
        with mock.patch("discord_alerts.sender.requests.get") as get, self.assertLogs("discord_alerts", "ERROR") as logs:
            get.return_value.status_code = 404
            get.return_value.raise_for_status.side_effect = error
            info = sender.lookup_webhook(hook(2), "a.bsky.social")
        self.assertFalse(info.ok)
        self.assertEqual(info.failure.category, "discord_webhook_invalid")
        self.assertNotIn("token-2", " ".join(logs.output) + info.failure.error + info.failure.technical)

    def test_a_missing_server_id_is_reported_as_unknown(self):
        with mock.patch("discord_alerts.sender.requests.get") as get:
            get.return_value.status_code = 200
            get.return_value.json.return_value = {"id": "9"}
            self.assertEqual(sender.lookup_webhook(hook(2)).guild_id, "")


class MentionDeliveryTests(DestinationTestCase):
    def test_each_channel_gets_its_own_role_or_none(self):
        self.send_to(self.account, (self.one, ROLE), (self.two, ""))
        self.check("a")
        self.assertEqual(self.discord.to("token-2"), [("a", ROLE)])
        self.assertEqual(self.discord.to("token-3"), [("a", "")])
        self.assertEqual(self.discord.lookups, ["token-2"], "the server is only asked about a channel with a role")

    def test_the_server_is_asked_once_per_channel(self):
        self.send_to(self.account, (self.one, ROLE))
        self.check("a")
        self.check("b", "a")
        self.assertEqual(self.discord.lookups, ["token-2"])
        self.assertEqual(DiscordWebhook.objects.get(pk=self.one.pk).guild_id, GUILD)

    def test_a_role_id_equal_to_the_server_id_is_never_mentioned(self):
        self.send_to(self.account, (self.one, GUILD))
        self.check("a")
        self.assertEqual(self.discord.to("token-2"), [("a", "")], "sent, without the mention")
        event = ActivityEvent.objects.get(type="mention_not_sent")
        self.assertEqual((event.category, event.level, event.channel_label), ("mention_refused", "warning", "#one"))

    def test_a_server_discord_did_not_name_means_no_mention(self):
        self.discord.lookup = lambda url, label="": WebhookInfo(True, "")
        with mock.patch("discord_alerts.sender.lookup_webhook", side_effect=self.discord.lookup):
            self.send_to(self.account, (self.one, ROLE))
            self.check("a")
        self.assertEqual(self.discord.to("token-2"), [("a", "")])
        self.assertTrue(ActivityEvent.objects.filter(type="mention_not_sent").exists())

    def test_if_discord_cannot_be_asked_the_post_waits_and_nothing_is_posted(self):
        failure = DeliveryResult(False, "Couldn't reach Discord.", category="discord_unavailable")
        with mock.patch("discord_alerts.sender.lookup_webhook", return_value=WebhookInfo(False, failure=failure)):
            self.send_to(self.account, (self.one, ROLE))
            self.check("a")
        self.assertEqual(self.discord.sent, [])
        self.assertEqual(self.delivery("a", self.one).status, PostDelivery.RETRY)

    def test_a_changed_webhook_url_is_asked_again(self):
        DiscordWebhook.objects.filter(pk=self.one.pk).update(guild_id=GUILD)
        res = self.client.patch(f"/api/webhooks/{self.one.pk}/", {"url": hook(9)}, content_type="application/json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(DiscordWebhook.objects.get(pk=self.one.pk).guild_id, "")

    def test_a_mention_is_logged_with_the_send(self):
        self.send_to(self.account, (self.one, ROLE))
        self.check("a")
        event = ActivityEvent.objects.get(type="post_sent")
        self.assertIn("with the role mention", event.message)
        self.assertEqual(event.detail["role_id"], ROLE)


# -- checking Role IDs -------------------------------------------------------------------------
class RoleIdTests(TestCase):
    def test_accepted(self):
        for typed, stored in [("", ""), ("   ", ""), (ROLE, ROLE), (f"  {ROLE} ", ROLE), (f"<@&{ROLE}>", ROLE),
                              ("1" * 17, "1" * 17), (str(2 ** 64 - 1), str(2 ** 64 - 1))]:
            with self.subTest(typed=typed):
                self.assertEqual(mentions.clean_role_id(typed), stored)

    def test_refused(self):
        for typed in ["everyone", "@everyone", "@EVERYONE", "here", "@here", "abc", "1" * 16, "1" * 21,
                      str(2 ** 64), f"-{ROLE}", f"{ROLE[:9]} {ROLE[9:]}", f"<@{ROLE}>", f"<#{ROLE}>", f"<@&{ROLE}",
                      f"{ROLE}x", "１１８９０００００００００００００１２３"]:
            with self.subTest(typed=typed), self.assertRaises(mentions.InvalidRoleId):
                mentions.clean_role_id(typed)

    def test_everyone_gets_its_own_explanation(self):
        with self.assertRaises(mentions.InvalidRoleId) as caught:
            mentions.clean_role_id("@here")
        self.assertIn("@everyone and @here can't be mentioned", caught.exception.message)


@override_settings(DISCORD_WEBHOOK_URL="")
class DestinationApiTests(APITestCase):
    url = "/api/accounts/"

    def setUp(self):
        self.main = channels.make_default(DiscordWebhook.objects.create(name="#main", url=hook(1)))
        self.one = DiscordWebhook.objects.create(name="#one", url=hook(2))
        self.two = DiscordWebhook.objects.create(name="#two", url=hook(3))

    def add(self, destinations):
        return self.client.post(self.url, {"handle": "a.bsky.social", "destinations": destinations}, format="json")

    def test_several_channels_with_and_without_a_role(self):
        res = self.add([{"channel_id": self.one.id, "role_id": f" <@&{ROLE}> "}, {"channel_id": self.two.id},
                        {"channel_id": None, "role_id": ""}])
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual([(d["channel_id"], d["role_id"], d["follows_default"]) for d in res.data["destinations"]],
                         [(self.one.id, ROLE, False), (self.two.id, "", False), (None, "", True)])
        self.assertEqual(channel_ids(MonitoredAccount.objects.get()), [self.one.id, self.two.id, None])

    def test_a_bad_role_id_is_reported_on_its_row(self):
        res = self.add([{"channel_id": self.one.id, "role_id": ""}, {"channel_id": self.two.id, "role_id": "@everyone"}])
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["destinations"][0], {})
        self.assertIn("@everyone and @here can't be mentioned", str(res.data["destinations"][1]["role_id"][0]))
        self.assertFalse(MonitoredAccount.objects.exists(), "nothing is saved")

    def test_letters_are_not_a_role_id(self):
        res = self.add([{"channel_id": self.one.id, "role_id": "moderators"}])
        self.assertIn("A Role ID is a long number", str(res.data["destinations"][0]["role_id"][0]))

    def test_the_servers_own_id_is_refused_once_it_is_known(self):
        DiscordWebhook.objects.filter(pk=self.one.pk).update(guild_id=GUILD)
        res = self.add([{"channel_id": self.one.id, "role_id": GUILD}])
        self.assertEqual(res.status_code, 400)
        self.assertIn("server's ID", str(res.data["destinations"][0]["role_id"][0]))
        DiscordWebhook.objects.filter(pk=self.main.pk).update(guild_id=GUILD)
        res = self.add([{"channel_id": None, "role_id": GUILD}])
        self.assertIn("server's ID", str(res.data["destinations"][0]["role_id"][0]), "also for the default channel")

    def test_list_rules(self):
        cases = {
            "empty": ([], "Choose at least one channel"),
            "too many": ([{"channel_id": None}] + [{"channel_id": DiscordWebhook.objects.create(name=f"#c{n}", url=hook(
                100 + n)).id} for n in range(10)], "at most 10 channels"),
        }
        for name, (destinations, message) in cases.items():
            with self.subTest(name):
                res = self.add(destinations)
                self.assertEqual(res.status_code, 400)
                self.assertIn(message, str(res.data["destinations"]))

    def test_each_channel_once(self):
        res = self.add([{"channel_id": self.one.id}, {"channel_id": self.one.id}])
        self.assertIn("already in the list", str(res.data["destinations"][1]["channel_id"]))
        res = self.add([{"channel_id": None}, {"channel_id": None}])
        self.assertIn("default channel is already in the list", str(res.data["destinations"][1]["channel_id"]))
        res = self.add([{"channel_id": None}, {"channel_id": self.main.id}])
        self.assertIn("is the default channel", str(res.data["destinations"][1]["channel_id"]))

    def test_ten_channels_are_allowed(self):
        many = [{"channel_id": DiscordWebhook.objects.create(name=f"#c{n}", url=hook(100 + n)).id} for n in range(10)]
        self.assertEqual(self.add(many).status_code, 201)

    def test_saving_replaces_the_list_and_leaving_it_out_keeps_it(self):
        account_id = self.add([{"channel_id": self.one.id, "role_id": ROLE}]).data["id"]
        self.client.patch(f"{self.url}{account_id}/", {"display_name": "A"}, format="json")
        self.assertEqual(channel_ids(MonitoredAccount.objects.get()), [self.one.id])
        res = self.client.patch(f"{self.url}{account_id}/", {"destinations": [{"channel_id": self.two.id}]},
                                format="json")
        self.assertEqual([d["channel_id"] for d in res.data["destinations"]], [self.two.id])

    def test_the_posts_list_shows_each_delivery(self):
        account = MonitoredAccount.objects.create(handle="a.bsky.social", first_poll_done=True)
        channels.set_destinations(account, [{"channel": self.one, "role_id": ROLE}, {"channel": self.two, "role_id": ""}])
        poller.claim_post(account, feed_item("a"), SeenPost.PENDING)
        post = self.client.get(f"{self.url}{account.id}/posts/").data["results"][0]
        self.assertEqual(post["status"], "pending")
        self.assertEqual([(d["channel"]["name"], d["role_id"], d["status"]) for d in post["deliveries"]],
                         [("#one", ROLE, "pending"), ("#two", "", "pending")])


# -- old posts are never sent again --------------------------------------------------------------
class NoResendTests(DestinationTestCase):
    def test_adding_a_channel_only_covers_new_posts(self):
        self.send_to(self.account, (self.one, ""))
        self.check("a")
        self.send_to(self.account, (self.one, ""), (self.two, ""))
        self.check("b", "a")
        self.assertEqual(self.discord.to("token-3"), [("b", "")], "a is not sent to the new channel")
        self.assertEqual(self.discord.to("token-2"), [("a", ""), ("b", "")])

    def test_changing_a_role_affects_only_new_posts(self):
        self.send_to(self.account, (self.one, ROLE))
        self.discord.down["token-2"] = DeliveryResult(False, "down", category="discord_unavailable")
        self.check("a")  # a waits for a retry, with the role it had
        self.send_to(self.account, (self.one, ROLE_2))
        del self.discord.down["token-2"]
        self.clock.advance(minutes=2)
        self.check("b", "a")
        self.assertEqual(self.discord.to("token-2"), [("a", ROLE), ("b", ROLE_2)])
        self.check("b", "a")
        self.assertEqual(len(self.discord.sent), 2, "nothing is sent again")

    def test_changing_the_default_never_resends(self):
        self.send_to(self.account, (None, ""))
        self.check("a")
        channels.make_default(self.two)
        self.check("b", "a")
        self.assertEqual(self.discord.to("token-1"), [("a", "")])
        self.assertEqual(self.discord.to("token-3"), [("b", "")])

    def test_unsent_posts_keep_going_where_they_were_going_when_the_default_changes(self):
        self.send_to(self.account, (None, ""))
        self.discord.down["token-1"] = DeliveryResult(False, "down", category="discord_unavailable")
        self.check("a")
        channels.make_default(self.two)
        del self.discord.down["token-1"]
        self.clock.advance(minutes=2)
        self.check("a")
        self.assertEqual(self.discord.to("token-1"), [("a", "")])
        self.assertEqual(self.discord.to("token-3"), [])

    def test_removing_a_channel_cancels_its_unsent_posts(self):
        self.discord.down["token-3"] = DeliveryResult(False, "down", category="discord_unavailable")
        self.check("b", "a")  # a and b: sent to #one, waiting for #two (retry and pending)
        self.assertEqual({self.delivery(r, self.two).status for r in "ab"}, {PostDelivery.RETRY, PostDelivery.PENDING})
        self.send_to(self.account, (self.one, ""))
        self.assertEqual({self.delivery(r, self.two).status for r in "ab"}, {PostDelivery.CANCELLED})
        self.assertEqual({self.post(r).status for r in "ab"}, {SeenPost.SENT})
        del self.discord.down["token-3"]
        self.clock.advance(hours=1)
        self.check("b", "a")
        self.assertEqual(self.discord.to("token-3"), [], "nothing arrives in the removed channel")

    def test_a_send_in_progress_is_not_cancelled(self):
        poller.claim_post(self.account, feed_item("a"), SeenPost.PENDING)
        PostDelivery.objects.filter(channel=self.two).update(status=PostDelivery.SENDING, claim_token="busy",
                                                             claimed_at=T0)
        self.send_to(self.account, (self.one, ""))
        self.assertEqual(self.delivery("a", self.two).status, PostDelivery.SENDING, "it may already be in Discord")
        PostDelivery.objects.filter(channel=self.two, claim_token="busy").update(status=PostDelivery.SENT)
        poller.refresh_post_status(self.post("a").pk)
        self.assertEqual(self.post("a").status, SeenPost.PENDING, "#one is still to be sent")

    def test_removing_every_channel_but_the_failing_one_still_cancels_nothing_sent(self):
        self.check("a")
        self.send_to(self.account, (self.two, ""))
        self.assertEqual(self.delivery("a", self.one).status, PostDelivery.SENT, "history is kept")


# -- deleting or moving a channel ----------------------------------------------------------------
class DeleteChannelTests(DestinationTestCase):
    def wait_on_two(self, *rkeys):
        self.discord.down["token-3"] = DeliveryResult(False, "down", category="discord_unavailable")
        self.check(*reversed(rkeys))

    def test_unsent_posts_move_with_the_accounts(self):
        three = DiscordWebhook.objects.create(name="#three", url=hook(4))
        self.wait_on_two("a")
        channels.delete_channel(self.two, reassign_to=three)
        self.assertEqual(channel_ids(self.account), [self.one.pk, three.pk])
        self.assertEqual(self.delivery("a", three).status, PostDelivery.RETRY, "attempts and retry time kept")
        self.clock.advance(minutes=2)
        self.check("a")
        self.assertEqual(self.discord.to("token-4"), [("a", "")])

    def test_moving_to_a_channel_the_post_already_went_to_sends_nothing_twice(self):
        self.send_to(self.account, (self.one, ROLE), (self.two, ROLE_2))
        self.wait_on_two("a")
        channels.delete_channel(self.two, reassign_to=self.one)
        self.assertEqual(list(AccountDestination.objects.values_list("channel_id", "role_id")), [(self.one.pk, ROLE)],
                         "the account's own #one destination and role stay")
        self.assertEqual(PostDelivery.objects.filter(post=self.post("a")).count(), 1, "the waiting one was cancelled")
        self.clock.advance(hours=1)
        self.check("a")
        self.assertEqual(self.discord.to("token-2"), [("a", ROLE)])

    def test_moving_to_the_default(self):
        self.wait_on_two("a")
        channels.delete_channel(self.two, reassign_to="default")
        self.assertEqual(channel_ids(self.account), [self.one.pk, None])
        self.assertEqual(self.delivery("a", self.main).status, PostDelivery.RETRY)

    def test_moving_an_account_that_already_follows_the_default(self):
        self.send_to(self.account, (None, ""), (self.two, ROLE))
        channels.delete_channel(self.two, reassign_to="default")
        self.assertEqual(list(AccountDestination.objects.values_list("channel_id", "role_id")), [(None, "")])

    def test_a_send_in_progress_is_not_moved(self):
        three = DiscordWebhook.objects.create(name="#three", url=hook(4))
        poller.claim_post(self.account, feed_item("a"), SeenPost.PENDING)
        PostDelivery.objects.filter(channel=self.two).update(status=PostDelivery.SENDING, claim_token="busy",
                                                             claimed_at=T0)
        channels.delete_channel(self.two, reassign_to=three)
        self.assertFalse(PostDelivery.objects.filter(channel=three).exists(), "it may already be in Discord")
        self.clock.advance(hours=1)
        self.check("a")
        self.assertEqual(self.discord.to("token-4"), [])

    def test_a_delivery_moved_while_being_read_is_not_sent_to_the_old_channel(self):
        three = DiscordWebhook.objects.create(name="#three", url=hook(4))
        poller.claim_post(self.account, feed_item("a"), SeenPost.PENDING)
        stale_view = list(poller._due_for_delivery(self.account, self.clock()))  # read before the move
        PostDelivery.objects.filter(channel=self.two).update(channel=three)
        with mock.patch.object(poller, "_due_for_delivery", return_value=stale_view):
            poller.deliver_pending(self.account)
        self.assertEqual(self.discord.to("token-3"), [], "the moved delivery isn't claimed for its old channel")
        poller.deliver_pending(self.account)
        self.assertEqual(self.discord.to("token-4"), [("a", "")])


# -- status page and clean-up --------------------------------------------------------------------
class StatusAndCleanupTests(DestinationTestCase):
    def test_counts_are_per_notification(self):
        self.discord.down["token-3"] = DeliveryResult(False, "down", category="discord_unavailable")
        self.check("a")
        counts = bot_status.compute()["counts"]
        self.assertEqual((counts["sent_last_24h"], counts["deliveries_retrying"], counts["pending_deliveries"]),
                         (1, 1, 1))

    def test_an_account_with_nowhere_to_send_is_a_problem(self):
        channels.clear_default()
        self.send_to(self.account, (None, ""))
        codes = [p["code"] for p in bot_status.compute()["problems"]]
        self.assertIn("no_channel", codes)
        self.send_to(self.account, (self.one, ""))
        self.assertNotIn("no_channel", [p["code"] for p in bot_status.compute()["problems"]])

    def test_clean_up_keeps_posts_still_waiting_for_any_channel(self):
        self.discord.down["token-3"] = DeliveryResult(False, "down", category="discord_unavailable")
        self.check("a")
        del self.discord.down["token-3"]
        self.check("c", "b", "a")  # b and c are sent; a's retry isn't due yet
        self.assertEqual(retention.prune_seen_posts(keep=1), 1, "b goes; a still waits for #two")
        self.assertEqual(sorted(p.post_uri[-1] for p in SeenPost.objects.all()), ["a", "c"])
        self.clock.advance(minutes=2)
        self.check("c", "a")
        self.assertEqual(self.post("a").status, SeenPost.SENT)
        self.assertEqual(retention.prune_seen_posts(keep=1), 1)
        self.assertEqual(PostDelivery.objects.filter(post__post_uri__endswith="/a").count(), 0, "deleted with it")

    def test_activity_clean_up_never_touches_deliveries(self):
        self.check("a")
        ActivityEvent.objects.all().delete()
        activity_retention.prune()
        self.assertEqual(PostDelivery.objects.count(), 2)


# -- the database upgrade ----------------------------------------------------------------------
class UpgradeMigrationTests(TransactionTestCase):
    """From the rc.1 database layout (bluesky_monitor 0007) to destinations and deliveries, and back."""
    before = [("bluesky_monitor", "0007_remove_legacy_webhook_url")]
    after_ours = [("bluesky_monitor", "0009_remove_single_channel_fields")]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())

    def old_data(self):
        old = self.migrate(self.before)
        Webhook = old.get_model("discord_alerts", "DiscordWebhook")
        Account = old.get_model("bluesky_monitor", "MonitoredAccount")
        Post = old.get_model("bluesky_monitor", "SeenPost")
        main = Webhook.objects.create(name="#main", url=hook(1), is_default=True)
        art = Webhook.objects.create(name="#art", url=hook(2))
        own = Account.objects.create(handle="own.bsky.social", channel=art, first_poll_done=True)
        follower = Account.objects.create(handle="follower.bsky.social", first_poll_done=True)
        due = T0 + timedelta(minutes=5)
        for account, rkey, status, extra in [
            (own, "pending", "pending", {}),
            (own, "retry", "retry", {"attempts": 2, "next_attempt_at": due, "last_error": "down"}),
            (follower, "sending", "sending", {"attempts": 1, "claim_token": "t", "claimed_at": T0}),
            (follower, "sent", "sent", {"attempts": 1, "sent_at": T0}),
            (follower, "failed", "failed", {"attempts": 4, "last_error": "gave up"}),
            (follower, "seeded", "seeded", {}),
        ]:
            Post.objects.create(account=account, post_uri=f"at://x/app.bsky.feed.post/{rkey}", post_cid=rkey,
                                status=status, **extra)
        return main, art, due

    def test_routes_and_waiting_posts_are_kept(self):
        main, art, due = self.old_data()
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        dest = dict(AccountDestination.objects.values_list("account__handle", "channel_id"))
        self.assertEqual(dest, {"own.bsky.social": art.pk, "follower.bsky.social": None})
        self.assertFalse(AccountDestination.objects.exclude(role_id="").exists(), "no roles yet")
        rows = {d.post.post_uri.rsplit("/", 1)[1]: d for d in PostDelivery.objects.select_related("post")}
        self.assertEqual(set(rows), {"pending", "retry", "sending"}, "only posts still waiting get a delivery")
        self.assertEqual((rows["pending"].channel_id, rows["pending"].status), (art.pk, "pending"))
        self.assertEqual((rows["retry"].channel_id, rows["retry"].status, rows["retry"].attempts,
                          rows["retry"].next_attempt_at, rows["retry"].last_error),
                         (art.pk, "retry", 2, due, "down"))
        self.assertEqual((rows["sending"].channel_id, rows["sending"].claim_token), (main.pk, "t"),
                         "a follower's post goes to the default, as before")
        statuses = dict((p.post_uri.rsplit("/", 1)[1], p.status) for p in SeenPost.objects.all())
        self.assertEqual(statuses, {"pending": "pending", "retry": "pending", "sending": "pending",
                                    "sent": "sent", "failed": "failed", "seeded": "seeded"})

    def test_a_waiting_post_with_nowhere_to_go_is_skipped(self):
        old = self.migrate(self.before)
        Account = old.get_model("bluesky_monitor", "MonitoredAccount")
        Post = old.get_model("bluesky_monitor", "SeenPost")
        account = Account.objects.create(handle="lost.bsky.social")
        Post.objects.create(account=account, post_uri="at://x/app.bsky.feed.post/1", post_cid="1", status="retry")
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        self.assertEqual(SeenPost.objects.get().status, "skipped")
        self.assertFalse(PostDelivery.objects.exists())

    def test_going_back_keeps_the_first_channel_and_one_delivery_but_not_the_rest(self):
        """Lossy by design (see the 0008 migration): the pre-upgrade backup is the full rollback."""
        main = channels.make_default(DiscordWebhook.objects.create(name="#main", url=hook(1)))
        art = DiscordWebhook.objects.create(name="#art", url=hook(2))
        account = MonitoredAccount.objects.create(handle="a.bsky.social", first_poll_done=True)
        channels.set_destinations(account, [{"channel": art, "role_id": ROLE}, {"channel": None, "role_id": ""}])
        poller.claim_post(account, feed_item("w"), SeenPost.PENDING)
        PostDelivery.objects.filter(channel=main).update(status="retry", attempts=3)
        poller.claim_post(account, feed_item("p"), SeenPost.PENDING)
        PostDelivery.objects.filter(post__post_uri__endswith="/p", channel=art).update(status="sent")
        PostDelivery.objects.filter(post__post_uri__endswith="/p", channel=main).update(status="failed")
        poller.refresh_post_status(SeenPost.objects.get(post_uri__endswith="/p").pk)

        old = self.migrate(self.before)
        Account = old.get_model("bluesky_monitor", "MonitoredAccount")
        Post = old.get_model("bluesky_monitor", "SeenPost")
        self.assertEqual(Account.objects.get().channel_id, art.pk, "only the first channel; the role is gone")
        waiting = Post.objects.get(post_uri__endswith="/w")
        self.assertEqual((waiting.status, waiting.attempts), ("pending", 0),
                         "the first unsent delivery (#art, pending) is kept; #main's retry state is lost")
        self.assertEqual(Post.objects.get(post_uri__endswith="/p").status, "sent", "partly sent reads as sent")
        with connection.cursor() as cursor:
            tables = connection.introspection.table_names(cursor)
        self.assertNotIn("bluesky_monitor_accountdestination", tables)
        self.assertNotIn("bluesky_monitor_postdelivery", tables)
