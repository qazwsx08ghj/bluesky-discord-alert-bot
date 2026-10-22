"""The API contract (docs/openapi.yaml) matches what the API really does.

1. The committed contract is up to date with the code (`manage.py api_contract`).
2. Real responses of every operation, in many states and for the documented errors, are
   validated against the contract. Objects are treated as closed: a field the code returns
   that the contract doesn't list fails, just like a documented field that's missing.
3. Every operation in the contract is exercised here at least once, so a new endpoint
   can't be added without being checked.

Bluesky and Discord are faked; no network is used. Needs requirements-dev.txt.
"""
import copy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, tag
from django.utils import timezone
from jsonschema import Draft202012Validator
from rest_framework.test import APIClient

from activity import log as activity
from bluesky_monitor import bluesky, jobs, locks, profiles, scheduler
from bluesky_monitor.models import BlueskySessionState, MonitoredAccount, PostDelivery, SchedulerState, SeenPost
from bluesky_monitor.tests import FakeSession, claim_with_state, feed_item, with_channel
from botconfig import store
from discord_alerts import channels
from discord_alerts.models import DiscordWebhook
from discord_alerts.sender import DeliveryResult

CONTRACT = Path(settings.BASE_DIR) / "docs" / "openapi.yaml"
SPEC = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
HOOK = "https://discord.com/api/webhooks/100/contract-test-token-aaaaaaaaaaaaaaaa"
HOOK2 = "https://discord.com/api/webhooks/200/contract-test-token-bbbbbbbbbbbbbbbb"
PASSWORD = "abcd-efgh-ijkl-mnop"


def _closed(node):
    """Deep copy where every object with listed properties allows no other properties."""
    node = copy.deepcopy(node)

    def walk(value):
        if isinstance(value, dict):
            if "properties" in value and "additionalProperties" not in value:
                value["additionalProperties"] = False
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(node)
    return node


COMPONENTS = _closed(SPEC["components"])


def validator(schema):
    return Draft202012Validator({"components": COMPONENTS, **_closed(schema)})


@tag("contract")
class ContractTestCase(TestCase):
    checked = set()  # (path, method, status) seen by any test in this module

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        for target, value in (("discord_alerts.sender.deliver_alert", DeliveryResult(True)),):
            patcher = mock.patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def check(self, response, path, method="get"):
        """Validate a response against the contract's description of it."""
        operation = SPEC["paths"][path][method]
        status = str(response.status_code)
        self.assertIn(status, operation["responses"],
                      f"{method.upper()} {path} answered {status}, which the contract doesn't list: {response.content[:300]!r}")
        ContractTestCase.checked.add((path, method, status))
        content = operation["responses"][status].get("content")
        if not content:
            self.assertEqual(response.content, b"", f"{method.upper()} {path} {status} should have no body")
            return None
        body = response.json()
        errors = sorted(validator(content["application/json"]["schema"]).iter_errors(body), key=str)
        self.assertEqual([], [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors][:10],
                         f"{method.upper()} {path} {status} doesn't match the contract")
        return body

    def check_request(self, body, path, method):
        """The body the panel sends is a valid request according to the contract."""
        schema = SPEC["paths"][path][method]["requestBody"]["content"]["application/json"]["schema"]
        errors = list(validator(schema).iter_errors(body))
        self.assertEqual([], [e.message for e in errors], f"request for {method.upper()} {path}")

    def setup_bot(self):
        store.save({"bluesky_handle": "bot.bsky.social", "bluesky_password": PASSWORD, "poll_interval_minutes": 5})
        self.main = DiscordWebhook.objects.create(name="announcements", url=HOOK)
        channels.make_default(self.main)
        self.art = DiscordWebhook.objects.create(name="art", url=HOOK2)
        scheduler.heartbeat("T")


ROLE = "1189000000000000123"


@tag("contract")
class ContractFileTests(TestCase):
    def test_the_committed_contract_matches_the_code(self):
        from config.openapi import generate

        text, problems = generate()
        self.assertFalse(problems, "drf-spectacular reported warnings; see the output above")
        current = CONTRACT.read_bytes().decode("utf-8").replace("\r\n", "\n")
        self.assertEqual(current, text, "docs/openapi.yaml is out of date: run "
                                        "`python manage.py api_contract --write` and `npm run api:types`")

    def test_the_contract_never_shows_secrets_in_responses(self):
        for name, schema in SPEC["components"]["schemas"].items():
            if name.endswith("Request"):
                continue
            secret = ("bluesky_password", "password", "session_string", "secret_key")
            if name in ("Channel", "Account", "Settings"):
                secret += ("url", "discord_webhook_url")  # webhook URLs are write-only; url_masked is shown
            for field in schema.get("properties", {}):
                with self.subTest(schema=name, field=field):
                    self.assertNotIn(field, secret)

    def test_the_contract_is_stable_text(self):
        raw = CONTRACT.read_bytes()
        self.assertNotIn(b"\r\n", raw, "LF line endings, so it's identical on Windows and Linux")
        self.assertEqual(SPEC["openapi"], "3.1.0")


class StatusAndHealthTests(ContractTestCase):
    def test_health_ready_and_updating(self):
        self.check(self.client.get("/api/health/"), "/api/health/")
        with mock.patch("botconfig.management.commands.backup_db.pending_migrations", return_value=["x"]):
            body = self.check(self.client.get("/api/health/"), "/api/health/")
        self.assertEqual(body["status"], "updating")

    def test_csrf(self):
        self.check(self.client.get("/api/csrf/"), "/api/csrf/")

    def test_status_in_every_state(self):
        body = self.check(self.client.get("/api/status/"), "/api/status/")
        self.assertEqual(body["state"], "unavailable")  # no checker yet, nothing set up
        scheduler.heartbeat("T")
        body = self.check(self.client.get("/api/status/"), "/api/status/")
        self.assertEqual(body["state"], "not_configured")
        self.setup_bot()
        MonitoredAccount.objects.create(handle="a.bsky.social", first_poll_done=True)
        now = timezone.now()
        SchedulerState.objects.filter(pk=1).update(cycle_started_at=now - timedelta(minutes=1), cycle_finished_at=now,
                                                   last_cycle_summary={"result": "partial", "category": "bluesky_error",
                                                                       "accounts_checked": 1, "accounts_failed": 1,
                                                                       "posts_sent": 0})
        body = self.check(self.client.get("/api/status/"), "/api/status/")
        self.assertIsNotNone(body["scheduler"]["last_check"])
        # A rejected login, a retrying post and a recent error: problems of every kind.
        from bluesky_monitor.bluesky import _fingerprint
        BlueskySessionState.objects.update_or_create(pk=1, defaults=dict(
            handle="bot.bsky.social", credential_fingerprint=_fingerprint("bot.bsky.social", PASSWORD),
            login_failures=1, login_retry_after=now + timedelta(minutes=10)))
        acc = MonitoredAccount.objects.get()
        claim_with_state(acc, feed_item("1"), PostDelivery.RETRY, next_attempt_at=now + timedelta(minutes=1))
        activity.problem(activity.SEND_FAILED, "discord_unavailable", "Couldn't send.", account=acc, channel=self.main,
                         level="warning", attempt=1)
        body = self.check(self.client.get("/api/status/"), "/api/status/")
        self.assertEqual(body["state"], "problem")
        self.assertTrue(body["problems"] and body["recent_errors"])


class SettingsTests(ContractTestCase):
    def test_read_and_change(self):
        self.check(self.client.get("/api/settings/"), "/api/settings/")
        self.setup_bot()
        self.check(self.client.get("/api/settings/"), "/api/settings/")
        change = {"bluesky_handle": "me.bsky.social", "bluesky_password": "", "poll_interval_minutes": 7}
        self.check_request(change, "/api/settings/", "patch")
        self.check(self.client.patch("/api/settings/", change, format="json"), "/api/settings/", "patch")
        self.check(self.client.post("/api/settings/", {"poll_interval_minutes": 9}, format="json"), "/api/settings/", "post")

    def test_errors(self):
        for body in ({"poll_interval_minutes": 0}, {"discord_webhook_url": HOOK}):
            self.check(self.client.patch("/api/settings/", body, format="json"), "/api/settings/", "patch")
        with mock.patch.object(store, "save", side_effect=store.SettingsSaveError("The settings couldn't be saved.")):
            self.check(self.client.patch("/api/settings/", {"poll_interval_minutes": 9}, format="json"),
                       "/api/settings/", "patch")
            self.check(self.client.post("/api/settings/", {"poll_interval_minutes": 9}, format="json"),
                       "/api/settings/", "post")
        self.check(self.client.post("/api/settings/", {"poll_interval_minutes": 61}, format="json"),
                   "/api/settings/", "post")
        res = self.client.generic("PATCH", "/api/settings/", "{not json", content_type="application/json")
        self.assertEqual(self.check(res, "/api/settings/", "patch")["code"], "invalid_json")

    def test_login_test_every_outcome(self):
        path = "/api/settings/test-login/"
        self.check(self.client.post(path, {}, format="json"), path, "post")  # 400: nothing to test
        self.setup_bot()
        outcomes = [bluesky.LoginTest("ok", did="did:plc:x", handle="bot.bsky.social"),
                    bluesky.LoginTest("rejected", "bluesky_login_rejected"),
                    bluesky.LoginTest("unavailable", "bluesky_unavailable"),
                    bluesky.LoginTest("error", "bluesky_error"),
                    bluesky.LoginTest("rate_limited", "bluesky_rate_limited", retry_after=60)]
        for outcome in outcomes:
            cache.clear()
            with self.subTest(result=outcome.result), mock.patch.object(bluesky, "try_login", return_value=outcome):
                request = {"bluesky_handle": "", "bluesky_password": ""}
                self.check_request(request, path, "post")
                self.check(self.client.post(path, request, format="json"), path, "post")
        cache.clear()
        with mock.patch.object(bluesky, "try_login", return_value=outcomes[1]):
            for _ in range(settings.LOGIN_TEST_LIMIT + 1):
                res = self.client.post(path, {}, format="json")
        self.assertEqual(self.check(res, path, "post")["code"], "too_many_tests")
        res = self.client.generic("POST", path, "{not json", content_type="application/json")
        self.assertEqual(self.check(res, path, "post")["code"], "invalid_json")


class AccountTests(ContractTestCase):
    def accounts_in_every_state(self):
        now = timezone.now()
        active = MonitoredAccount.objects.create(handle="active.bsky.social", first_poll_done=True, last_checked_at=now,
                                                 last_check_ok=True, last_success_at=now, first_checked_at=now,
                                                 first_check_posts=3)
        channels.set_destinations(active, [{"channel": self.art, "role_id": ROLE}, {"channel": None, "role_id": ""}])
        MonitoredAccount.objects.create(handle="paused.bsky.social", is_active=False, first_poll_done=True)
        MonitoredAccount.objects.create(handle="new.bsky.social")
        MonitoredAccount.objects.create(handle="broken.bsky.social", first_poll_done=True, last_checked_at=now,
                                        last_check_ok=False, last_check_category="account_not_found",
                                        last_error_at=now, last_error_category="account_not_found")
        busy = MonitoredAccount.objects.create(handle="busy.bsky.social", first_poll_done=True)
        locks.acquire_account_lock(busy.pk)
        MonitoredAccount.objects.create(handle="legacy.bsky.social", announce_posts=None, announce_reposts=None,
                                        announce_replies=None)
        return busy

    def test_list_create_read_change_delete(self):
        self.setup_bot()
        self.check(self.client.get("/api/accounts/"), "/api/accounts/")
        busy = self.accounts_in_every_state()
        body = self.check(self.client.get("/api/accounts/"), "/api/accounts/")
        self.assertEqual({a["state"] for a in body["results"]},
                         {"active", "paused", "waiting_first_check", "error", "checking"})
        new = {"handle": "@artist.bsky.social", "display_name": "Artist", "is_active": True,
               "destinations": [{"channel_id": self.art.id, "role_id": ROLE}, {"channel_id": None}],
               "announce_posts": True, "announce_reposts": False, "announce_replies": True}
        self.check_request(new, "/api/accounts/", "post")
        created = self.check(self.client.post("/api/accounts/", new, format="json"), "/api/accounts/", "post")
        detail = f"/api/accounts/{created['id']}/"
        self.check(self.client.get(detail), "/api/accounts/{id}/")
        self.check_request({"is_active": False}, "/api/accounts/{id}/", "patch")
        self.check(self.client.patch(detail, {"is_active": False, "destinations": [{"channel_id": None, "role_id": ""}]},
                                     format="json"),
                   "/api/accounts/{id}/", "patch")
        self.check(self.client.put(detail, {"handle": "artist.bsky.social", "display_name": "A"}, format="json"),
                   "/api/accounts/{id}/", "put")
        self.check(self.client.get(f"/api/accounts/{busy.id}/"), "/api/accounts/{id}/")
        self.check(self.client.delete(detail), "/api/accounts/{id}/", "delete")

    def test_account_errors(self):
        self.setup_bot()
        MonitoredAccount.objects.create(handle="taken.bsky.social")
        for body in ({"handle": "taken.bsky.social"}, {"handle": "has spaces"}, {"handle": "x.bsky.social",
                                                                                  "discord_webhook_url": HOOK},
                     {"handle": "y.bsky.social", "channel_id": 999},
                     {"handle": "y.bsky.social", "destinations": [{"channel_id": 999}]},
                     {"handle": "y.bsky.social", "destinations": []},
                     {"handle": "y.bsky.social", "destinations": [{"channel_id": None}, {"channel_id": None,
                                                                                         "role_id": "@everyone"}]}):
            self.check(self.client.post("/api/accounts/", body, format="json"), "/api/accounts/", "post")
        res = self.client.generic("POST", "/api/accounts/", "{not json", content_type="application/json")
        self.assertEqual(self.check(res, "/api/accounts/", "post")["code"], "invalid_json")
        for method in ("get", "patch", "put", "delete"):
            res = getattr(self.client, method)("/api/accounts/999/", {} if method in ("patch", "put") else None,
                                               format="json")
            self.check(res, "/api/accounts/{id}/", method)
        acc = MonitoredAccount.objects.get()
        self.check(self.client.patch(f"/api/accounts/{acc.id}/", {"handle": "has spaces"}, format="json"),
                   "/api/accounts/{id}/", "patch")
        self.check(self.client.put(f"/api/accounts/{acc.id}/", {}, format="json"), "/api/accounts/{id}/", "put")

    def test_check_now_and_posts(self):
        self.setup_bot()
        acc = MonitoredAccount.objects.create(handle="artist.bsky.social")
        path = "/api/accounts/{id}/poll_now/"
        with mock.patch("bluesky_monitor.poller._new_session",
                        side_effect=lambda: FakeSession(feed_item("2"), feed_item("1"))):
            first = self.check(self.client.post(f"/api/accounts/{acc.id}/poll_now/"), path, "post")
            self.assertTrue(first["was_first_poll"])
            self.check(self.client.post(f"/api/accounts/{acc.id}/poll_now/"), path, "post")
        with mock.patch("bluesky_monitor.poller._new_session",
                        side_effect=lambda: FakeSession(feed_item("3"), feed_item("2"), feed_item("1"))):
            body = self.check(self.client.post(f"/api/accounts/{acc.id}/poll_now/"), path, "post")
        self.assertEqual(body["new_posts_announced"], 1)
        with mock.patch("bluesky_monitor.poller._new_session",
                        side_effect=lambda: FakeSession(error=bluesky.BlueskyLoginError("rejected"))):
            body = self.check(self.client.post(f"/api/accounts/{acc.id}/poll_now/"), path, "post")
        self.assertIn("error", body)
        with mock.patch("bluesky_monitor.poller._new_session",
                        side_effect=lambda: FakeSession(error=bluesky.BlueskyUnavailable("down"))):
            self.check(self.client.post(f"/api/accounts/{acc.id}/poll_now/"), path, "post")
        locks.acquire_account_lock(acc.pk)
        self.check(self.client.post(f"/api/accounts/{acc.id}/poll_now/"), path, "post")  # 409
        self.check(self.client.post("/api/accounts/999/poll_now/"), path, "post")  # 404
        body = self.check(self.client.get(f"/api/accounts/{acc.id}/posts/"), "/api/accounts/{id}/posts/")
        self.assertEqual({p["status"] for p in body["results"]}, {"seeded", "sent"})
        self.check(self.client.get("/api/accounts/999/posts/"), "/api/accounts/{id}/posts/")


class CheckJobTests(ContractTestCase):
    def test_jobs_from_start_to_finish(self):
        self.check(self.client.get("/api/check-jobs/"), "/api/check-jobs/")
        self.check(self.client.get("/api/check-jobs/latest/"), "/api/check-jobs/latest/")  # 404
        self.check(self.client.get("/api/check-jobs/9b2e0e5e-0000-4000-8000-000000000000/"),
                   "/api/check-jobs/{job_id}/")  # 404
        self.setup_bot()
        MonitoredAccount.objects.create(handle="a.bsky.social", first_poll_done=True)
        MonitoredAccount.objects.create(handle="b.bsky.social", is_active=False)
        job = self.check(self.client.post("/api/accounts/check_all/"), "/api/accounts/check_all/", "post")
        again = self.check(self.client.post("/api/accounts/check_all/"), "/api/accounts/check_all/", "post")
        self.assertFalse(again["created"])
        detail = f"/api/check-jobs/{job['id']}/"
        self.assertEqual(self.check(self.client.get(detail), "/api/check-jobs/{job_id}/")["status"], "queued")
        with mock.patch("bluesky_monitor.poller._new_session", side_effect=lambda: FakeSession(feed_item("1"))):
            jobs.run_pending("T")
        body = self.check(self.client.get(detail), "/api/check-jobs/{job_id}/")
        self.assertTrue(body["final"])
        self.check(self.client.get("/api/check-jobs/latest/"), "/api/check-jobs/latest/")
        self.check(self.client.get("/api/check-jobs/?limit=5"), "/api/check-jobs/")


class ChannelTests(ContractTestCase):
    def test_list_create_read_change_test_delete(self):
        self.setup_bot()
        with_channel(MonitoredAccount.objects.create(handle="a.bsky.social"), self.art)
        self.check(self.client.get("/api/webhooks/"), "/api/webhooks/")
        new = {"name": "news", "url": "https://discord.com/api/webhooks/300/new-token", "is_default": False}
        self.check_request(new, "/api/webhooks/", "post")
        created = self.check(self.client.post("/api/webhooks/", new, format="json"), "/api/webhooks/", "post")
        detail = f"/api/webhooks/{created['id']}/"
        self.check(self.client.get(detail), "/api/webhooks/{id}/")
        self.check(self.client.patch(detail, {"name": "news2", "url": created["url_masked"]}, format="json"),
                   "/api/webhooks/{id}/", "patch")
        self.check(self.client.put(detail, {"name": "news3", "is_default": False}, format="json"),
                   "/api/webhooks/{id}/", "put")
        self.check(self.client.post(f"{detail}test/"), "/api/webhooks/{id}/test/", "post")
        with mock.patch("discord_alerts.sender.deliver_alert", return_value=DeliveryResult(False, "no")):
            self.check(self.client.post(f"{detail}test/"), "/api/webhooks/{id}/test/", "post")  # 502
        body = self.check(self.client.get("/api/webhooks/"), "/api/webhooks/")
        self.assertTrue(any(c["last_delivery"] for c in body["results"]))
        self.check(self.client.post(f"{detail}make_default/"), "/api/webhooks/{id}/make_default/", "post")
        self.check(self.client.post(f"/api/webhooks/{self.main.id}/make_default/"), "/api/webhooks/{id}/make_default/",
                   "post")
        self.check(self.client.delete(detail), "/api/webhooks/{id}/", "delete")

    def test_channel_errors(self):
        self.setup_bot()
        with_channel(MonitoredAccount.objects.create(handle="a.bsky.social"), self.art)
        for body in ({"name": "x"}, {"name": "ART", "url": HOOK2 + "x"}, {"name": "y", "url": "https://example.com/h"}):
            self.check(self.client.post("/api/webhooks/", body, format="json"), "/api/webhooks/", "post")
        art = f"/api/webhooks/{self.art.id}/"
        self.check(self.client.patch(art, {"url": "https://example.com/h"}, format="json"), "/api/webhooks/{id}/", "patch")
        self.check(self.client.put(art, {"url": "nope"}, format="json"), "/api/webhooks/{id}/", "put")
        body = self.check(self.client.delete(art), "/api/webhooks/{id}/", "delete")  # 409 in use
        self.assertTrue(body["accounts"])
        self.check(self.client.delete(f"/api/webhooks/{self.main.id}/"), "/api/webhooks/{id}/", "delete")  # default
        self.check(self.client.delete(f"{art}?reassign_to=999"), "/api/webhooks/{id}/", "delete")  # 400
        for method, path in (("get", "/api/webhooks/{id}/"), ("patch", "/api/webhooks/{id}/"),
                             ("put", "/api/webhooks/{id}/"), ("delete", "/api/webhooks/{id}/"),
                             ("post", "/api/webhooks/{id}/test/"), ("post", "/api/webhooks/{id}/make_default/")):
            url = path.replace("{id}", "999")
            res = getattr(self.client, method)(url, {} if method in ("patch", "put") else None, format="json")
            self.check(res, path, method)
        self.check(self.client.delete(f"{art}?reassign_to=default"), "/api/webhooks/{id}/", "delete")


class ActivityTests(ContractTestCase):
    def test_log_and_entries(self):
        self.setup_bot()
        acc = MonitoredAccount.objects.create(handle="a.bsky.social")
        activity.record(activity.POST_SENT, "success", "Sent.", account=acc, channel=self.main, attempt=2,
                        detail={"post_url": "https://bsky.app/profile/a/post/1", "post_text": "hi"})
        activity.problem(activity.LOGIN_FAILED, "bluesky_login_rejected", "Rejected.")
        activity.record(activity.FIRST_CHECK_DONE, "info", "Noted.", account=acc, detail={"seeded_posts": 3})
        body = self.check(self.client.get("/api/activity/?page_size=2"), "/api/activity/")
        self.assertTrue(body["next"])
        self.check(self.client.get("/api/activity/?group=problems&level=error,warning&account=1"), "/api/activity/")
        self.check(self.client.get("/api/activity/?group=bogus"), "/api/activity/")  # 400
        self.check(self.client.get("/api/activity/?since=yesterday"), "/api/activity/")  # 400
        entry = body["results"][0]["id"]
        self.check(self.client.get(f"/api/activity/{entry}/"), "/api/activity/{id}/")
        self.check(self.client.get("/api/activity/999999/"), "/api/activity/{id}/")


class ProfileLookupTests(ContractTestCase):
    def test_every_outcome(self):
        from atproto_client import exceptions as at_exc

        path = "/api/bluesky/profile/"
        response = SimpleNamespace(status_code=400, content=SimpleNamespace(error="InvalidRequest", message="x"))
        profile = SimpleNamespace(handle="artist.bsky.social", did="did:plc:a", display_name="Artist",
                                  avatar="https://cdn.bsky.app/a.jpg", description="", followers_count=1,
                                  posts_count=2)
        cases = [("artist.bsky.social", ("found", profile)),
                 ("nobody.bsky.social", ("not_found", {"reason": "InvalidRequest"})),
                 ("gone.bsky.social", ("unavailable", {"reason": "AccountDeactivated"})),
                 ("slow.bsky.social", ("rate_limited", {"retry_after": 30})),
                 ("down.bsky.social", ("error", {"category": "bluesky_unavailable", "technical": "x"})),
                 ("odd.bsky.social", ("error", {"category": "bluesky_error", "technical": "x"}))]
        for handle, answer in cases:
            cache.clear()
            with self.subTest(handle=handle), mock.patch.object(profiles, "_fetch", return_value=answer):
                self.check(self.client.get(path, {"handle": handle}), path)
        self.check(self.client.get(path, {"handle": "has spaces"}), path)  # 400
        MonitoredAccount.objects.create(handle="artist.bsky.social")
        cache.clear()
        with mock.patch.object(profiles, "_fetch", return_value=("found", profile)):
            body = self.check(self.client.get(path, {"handle": "artist.bsky.social"}), path)
        self.assertTrue(body["already_watched"])
        del at_exc, response


class ProtectionTests(ContractTestCase):
    def test_changes_without_the_token_or_json_are_refused_as_documented(self):
        self.setup_bot()
        strict = APIClient(enforce_csrf_checks=True)
        acc = MonitoredAccount.objects.create(handle="a.bsky.social")
        for path, method in (("/api/settings/", "patch"), ("/api/accounts/", "post"),
                             ("/api/accounts/{id}/", "delete"), ("/api/accounts/check_all/", "post")):
            url = path.replace("{id}", str(acc.id))
            res = getattr(strict, method)(url, {}, format="json")
            self.assertEqual(self.check(res, path, method)["code"], "csrf_failed")
        res = self.client.generic("POST", "/api/accounts/", "handle=x", content_type="application/x-www-form-urlencoded")
        self.assertEqual(self.check(res, "/api/accounts/", "post")["code"], "json_required")


@tag("contract")
class ZzCoverageTests(TestCase):
    """Runs last (test classes run in alphabetical order): every operation was checked."""

    def test_zz_every_operation_has_a_successful_check(self):
        import unittest

        expected = {(p, m) for p, ops in SPEC["paths"].items() for m in ops}
        seen_ok = {(p, m) for p, m, s in ContractTestCase.checked if s.startswith("2")}
        if not ContractTestCase.checked:
            raise unittest.SkipTest("run the whole config.tests_openapi module to check coverage")
        self.assertEqual(sorted(expected - seen_ok), [], "operations in the contract that no test checks")
