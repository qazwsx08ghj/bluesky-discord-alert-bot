"""Step 7: security (cross-site requests, local-only hosts, JSON only, headers, no admin)
and the API's error format. Settings tests use a temporary .env, never the real one.
"""
import json
from io import StringIO
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from bluesky_monitor.models import CheckJob, MonitoredAccount, SeenPost
from botconfig import store
from discord_alerts import channels
from discord_alerts.models import DiscordWebhook
from discord_alerts.sender import DeliveryResult
from .security import check_local_only
from .tests import TempEnvMixin

HOOK = "https://discord.com/api/webhooks/1/first-secret-token"
HOOK2 = "https://discord.com/api/webhooks/2/second-secret-token"
PASSWORD = "abcd-efgh-ijkl-mnop"
EVIL = "http://evil.example"


class SecurityTestCase(TempEnvMixin, TestCase):
    """A client that checks CSRF the way a real browser request is checked."""

    def setUp(self):
        super().setUp()
        self.client = APIClient(enforce_csrf_checks=True)
        self.channel = DiscordWebhook.objects.create(name="#general", url=HOOK)
        channels.make_default(self.channel)
        self.spare = DiscordWebhook.objects.create(name="#spare", url=HOOK2)
        self.account = MonitoredAccount.objects.create(handle="artist.bsky.social", first_poll_done=True)
        store.save({"bluesky_handle": "bot.bsky.social", "bluesky_password": PASSWORD})
        for target, value in (("discord_alerts.sender.deliver_alert", DeliveryResult(True)),
                              ("config.frontend_views.send_discord_alert", True),
                              ("discord_alerts.views.send_discord_alert", True)):
            patcher = mock.patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch("bluesky_monitor.bluesky.try_login")
        self.try_login = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch("bluesky_monitor.poller._new_session")
        self.session = patcher.start()
        self.addCleanup(patcher.stop)

    def token(self):
        """What the control panel does: the page (or /api/csrf/) sets the cookie with the token."""
        res = self.client.get("/api/csrf/")
        self.assertEqual(res.status_code, 200)
        return res.json()["csrf_token"]

    def changes(self):
        """Every request that changes something, with a body like the panel sends."""
        a, c, s = self.account.pk, self.channel.pk, self.spare.pk
        return [
            ("post", "/api/accounts/", {"handle": "new.bsky.social"}),
            ("patch", f"/api/accounts/{a}/", {"is_active": False}),
            ("put", f"/api/accounts/{a}/", {"handle": "artist.bsky.social", "is_active": False}),
            ("post", f"/api/accounts/{a}/poll_now/", None),
            ("post", "/api/accounts/check_all/", None),
            ("post", "/api/settings/", {"poll_interval_minutes": 9}),
            ("patch", "/api/settings/", {"poll_interval_minutes": 8}),
            ("post", "/api/settings/test-discord/", {"url": HOOK2}),
            ("post", "/api/settings/test-login/", {"bluesky_handle": "x.bsky.social", "bluesky_password": "pw"}),
            ("post", "/api/webhooks/", {"name": "#new", "url": HOOK2 + "x"}),
            ("patch", f"/api/webhooks/{s}/", {"name": "#renamed"}),
            ("post", f"/api/webhooks/{s}/make_default/", None),
            ("post", f"/api/webhooks/{c}/test/", None),
            ("post", f"/api/webhooks/{c}/make_default/", None),
            ("delete", f"/api/webhooks/{s}/", None),
            ("delete", f"/api/accounts/{a}/", None),
        ]

    def send(self, method, path, body, **headers):
        kwargs = {"format": "json"} if body is not None else {}
        return getattr(self.client, method)(path, body, **kwargs, **headers)

    def snapshot(self):
        row = store.get()
        return (
            list(MonitoredAccount.objects.order_by("id").values_list("handle", "is_active", "channel_id")),
            list(DiscordWebhook.objects.order_by("id").values_list("name", "url", "is_default")),
            (row.version, row.bluesky_password, row.poll_interval_minutes),
            CheckJob.objects.count(), SeenPost.objects.count(),
        )

    def assert_refused_and_unchanged(self, code, **headers):
        before = self.snapshot()
        for method, path, body in self.changes():
            res = self.send(method, path, body, **headers)
            self.assertEqual(res.status_code, 403, f"{method} {path}: {res.content!r}")
            self.assertEqual(res.json()["code"], code, f"{method} {path}")
            self.assertEqual(res["Content-Type"], "application/json")
        self.assertEqual(self.snapshot(), before, "a refused request changed nothing")
        self.try_login.assert_not_called()
        self.session.assert_not_called()


class CsrfTests(SecurityTestCase):
    def test_every_change_without_a_token_is_refused_and_changes_nothing(self):
        self.assert_refused_and_unchanged("csrf_failed")

    def test_a_wrong_token_is_refused(self):
        self.token()
        self.assert_refused_and_unchanged("csrf_failed", HTTP_X_CSRFTOKEN="x" * 64)

    def test_the_token_without_its_cookie_is_refused(self):
        token = self.token()
        self.client.cookies.clear()
        self.assert_refused_and_unchanged("csrf_failed", HTTP_X_CSRFTOKEN=token)

    def test_another_site_is_refused_even_with_a_valid_token(self):
        token = self.token()
        self.assert_refused_and_unchanged("cross_site_request", HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN=EVIL)

    def test_an_opaque_origin_is_refused(self):
        # Sandboxed iframes and some redirects send "Origin: null".
        token = self.token()
        self.assert_refused_and_unchanged("cross_site_request", HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN="null")

    def test_browser_cross_site_marker_is_refused_even_with_a_token(self):
        token = self.token()
        self.assert_refused_and_unchanged("cross_site_request", HTTP_X_CSRFTOKEN=token,
                                          HTTP_SEC_FETCH_SITE="cross-site")

    def test_same_site_other_port_is_refused_unless_trusted(self):
        token = self.token()
        headers = dict(HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN="http://localhost:5173", HTTP_SEC_FETCH_SITE="same-site")
        res = self.client.post("/api/accounts/check_all/", **headers)
        self.assertEqual((res.status_code, res.json()["code"]), (403, "cross_site_request"))
        with override_settings(CSRF_TRUSTED_ORIGINS=["http://localhost:5173"]):
            res = self.client.post("/api/accounts/check_all/", **headers)
        self.assertEqual(res.status_code, 202, "the React dev server can be allowed explicitly")

    def test_the_control_panel_page_can_make_changes(self):
        page = self.client.get("/")
        self.assertIn("csrftoken", page.cookies, "the page sets the token cookie")
        self.assertContains(page, "X-CSRFToken")
        token = page.cookies["csrftoken"].value
        headers = dict(HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN="http://testserver", HTTP_SEC_FETCH_SITE="same-origin")
        res = self.client.post("/api/settings/", {"poll_interval_minutes": 9}, format="json", **headers)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(store.get().poll_interval_minutes, 9)

    def test_every_change_works_with_the_token(self):
        token = self.token()
        self.try_login.return_value = mock.Mock(result="rejected", category="bluesky_login_rejected",
                                                did=None, handle=None, retry_after=None)
        self.session.return_value = mock.Mock(author_feed=mock.Mock(return_value=mock.Mock(feed=[], cursor=None)))
        for method, path, body in self.changes():
            res = self.send(method, path, body, HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN="http://testserver",
                            HTTP_SEC_FETCH_SITE="same-origin")
            self.assertLess(res.status_code, 300, f"{method} {path}: {res.status_code} {res.content!r}")
            self.assertNotIn(res.status_code, (403, 415))

    def test_apps_without_a_browser_send_the_token_from_the_csrf_endpoint(self):
        # e.g. a React Native app: no Origin or Sec-Fetch headers; it keeps the cookie and sends the token.
        body = self.client.get("/api/csrf/").json()
        self.assertEqual((body["header_name"], body["cookie_name"]), ("X-CSRFToken", "csrftoken"))
        res = self.client.post("/api/accounts/check_all/", HTTP_X_CSRFTOKEN=body["csrf_token"])
        self.assertEqual(res.status_code, 202)

    def test_reading_needs_no_token(self):
        for path in ("/api/accounts/", "/api/webhooks/", "/api/settings/", "/api/status/", "/api/activity/",
                     "/api/check-jobs/", "/api/csrf/", f"/api/accounts/{self.account.pk}/"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_refusals_never_echo_what_was_sent(self):
        res = self.client.post("/api/settings/", {"bluesky_password": PASSWORD, "discord_webhook_url": HOOK2},
                               format="json", HTTP_ORIGIN=EVIL)
        self.assertEqual(res.status_code, 403)
        self.assertNotIn(PASSWORD, res.content.decode())
        self.assertNotIn("second-secret-token", res.content.decode())


class CrossOriginReadTests(SecurityTestCase):
    def test_no_cross_origin_reading_is_allowed(self):
        for path in ("/api/settings/", "/api/accounts/", "/api/webhooks/"):
            res = self.client.get(path, HTTP_ORIGIN=EVIL)
            self.assertNotIn("Access-Control-Allow-Origin", res, path)
            self.assertNotIn("Access-Control-Allow-Credentials", res, path)

    def test_preflight_gives_no_permission(self):
        res = self.client.options("/api/settings/", HTTP_ORIGIN=EVIL, HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
                                  HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type,x-csrftoken")
        for header in ("Access-Control-Allow-Origin", "Access-Control-Allow-Methods", "Access-Control-Allow-Headers"):
            self.assertNotIn(header, res)


class JsonOnlyTests(SecurityTestCase):
    def test_form_and_text_bodies_are_refused_even_with_a_token(self):
        token = self.token()
        before = self.snapshot()
        for content_type, body in (("application/x-www-form-urlencoded", "poll_interval_minutes=9"),
                                   ("multipart/form-data; boundary=x", "--x\r\nContent-Disposition: form-data; "
                                    'name="poll_interval_minutes"\r\n\r\n9\r\n--x--\r\n'),
                                   ("text/plain", '{"poll_interval_minutes": 9}')):
            res = self.client.generic("POST", "/api/settings/", body, content_type=content_type,
                                      HTTP_X_CSRFTOKEN=token)
            self.assertEqual(res.status_code, 415, content_type)
            self.assertEqual(res.json()["code"], "json_required")
        self.assertEqual(self.snapshot(), before)

    def test_broken_json_is_a_clear_error(self):
        res = self.client.generic("POST", "/api/settings/", "{not json", content_type="application/json",
                                  HTTP_X_CSRFTOKEN=self.token())
        self.assertEqual((res.status_code, res.json()["code"]), (400, "invalid_json"))

    def test_actions_without_a_body_still_work(self):
        res = self.client.post("/api/accounts/check_all/", HTTP_X_CSRFTOKEN=self.token())
        self.assertEqual(res.status_code, 202)


class LocalOnlyTests(TestCase):
    @override_settings(ALLOWED_HOSTS=["localhost", "127.0.0.1", "[::1]"])
    def test_other_host_names_are_refused(self):
        # A DNS-rebinding page reaches 127.0.0.1 under its own name; that name isn't accepted.
        for host in ("evil.example", "evil.example:8000", "192.168.1.5:8000", "attacker.localhost.evil.example"):
            with self.assertLogs("django.security", "ERROR"):
                self.assertEqual(self.client.get("/api/settings/", HTTP_HOST=host).status_code, 400, host)
            with self.assertLogs("django.security", "ERROR"):
                res = self.client.post("/api/accounts/check_all/", HTTP_HOST=host)
            self.assertEqual(res.status_code, 400, host)
        self.assertFalse(CheckJob.objects.exists())
        for host in ("localhost:8000", "127.0.0.1:8000", "[::1]:8000", "localhost"):
            self.assertEqual(self.client.get("/api/settings/", HTTP_HOST=host).status_code, 200, host)

    def test_default_hosts_are_local(self):
        self.assertTrue(set(settings.ALLOWED_HOSTS) - {"testserver"} <= {"localhost", "127.0.0.1", "[::1]"},
                        settings.ALLOWED_HOSTS)

    def test_a_warning_appears_if_other_hosts_are_allowed(self):
        with override_settings(ALLOWED_HOSTS=["localhost", "*"]):
            warnings = check_local_only()
        self.assertEqual([w.id for w in warnings], ["alertbot.W001"])
        with override_settings(ALLOWED_HOSTS=["localhost", "127.0.0.1", "[::1]"]):
            self.assertEqual(check_local_only(), [])

    def test_start_script_listens_on_this_computer_only(self):
        script = (settings.BASE_DIR / "start.bat").read_text(encoding="utf-8")
        self.assertIn("runserver 127.0.0.1:8000", script)
        self.assertNotIn("0.0.0.0", script)


class HeaderTests(TestCase):
    def test_api_responses_are_not_cached_framed_or_sniffed(self):
        res = self.client.get("/api/settings/")
        self.assertEqual(res["Cache-Control"], "no-store")
        self.assertEqual(res["X-Frame-Options"], "DENY")
        self.assertEqual(res["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", res["Content-Security-Policy"])

    def test_the_page_cannot_be_framed(self):
        res = self.client.get("/")
        self.assertEqual(res["X-Frame-Options"], "DENY")
        self.assertEqual(res.cookies["csrftoken"]["samesite"], "Strict")

    def test_no_html_api_pages(self):
        # A browser opening an API address gets JSON, not DRF's browsable HTML page with forms.
        res = self.client.get("/api/accounts/", HTTP_ACCEPT="text/html,application/xhtml+xml,*/*;q=0.8")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/json")
        self.assertNotIn(b"<form", res.content)


class AdminRemovedTests(TestCase):
    def test_admin_pages_are_gone(self):
        for path in ("/admin/", "/admin/login/", "/admin/bluesky_monitor/monitoredaccount/"):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        self.assertNotIn("django.contrib.admin", settings.INSTALLED_APPS)

    def test_no_migrations_are_missing(self):
        call_command("makemigrations", check=True, dry_run=True, stdout=StringIO())  # exits if changes are missing

    def test_old_admin_tables_are_left_alone(self):
        # Databases from before step 7 still have the admin's log table; it stays and does no harm.
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE IF NOT EXISTS django_admin_log (id integer PRIMARY KEY, note text)")
            cursor.execute("INSERT INTO django_admin_log (note) VALUES ('kept')")
        call_command("migrate", verbosity=0)
        with connection.cursor() as cursor:
            cursor.execute("SELECT note FROM django_admin_log")
            self.assertEqual(cursor.fetchall(), [("kept",)])
        self.assertEqual(self.client.get("/api/accounts/").status_code, 200)

    def test_setup_no_longer_asks_for_an_admin_user(self):
        script = (settings.BASE_DIR / "setup.bat").read_text(encoding="utf-8")
        self.assertNotIn("createsuperuser", script)


class ErrorFormatTests(TestCase):
    def test_not_found_and_wrong_method_have_a_code(self):
        res = self.client.get("/api/accounts/999/")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["code"], "not_found")
        self.assertIn("detail", res.json(), "the old field is kept")
        res = self.client.get("/api/settings/test-login/")
        self.assertEqual((res.status_code, res.json()["code"]), (405, "method_not_allowed"))
        res = self.client.get("/api/no-such-endpoint/")
        self.assertEqual((res.status_code, res.json()["code"]), (404, "not_found"))

    def test_field_errors_keep_their_shape(self):
        res = self.client.post("/api/accounts/", {}, content_type="application/json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("handle", res.json())
        self.assertNotIn("code", res.json())

    def test_unexpected_errors_are_json_without_details(self):
        with mock.patch("bluesky_monitor.views.MonitoredAccountViewSet.list",
                        side_effect=RuntimeError(f"bug near {PASSWORD} {HOOK}")),                 self.assertLogs("django.request", "ERROR"):
            res = self.client.get("/api/accounts/")
        self.assertEqual(res.status_code, 500)
        self.assertEqual(res.json()["code"], "internal")
        self.assertNotIn(PASSWORD, res.content.decode())
        self.assertNotIn("first-secret-token", res.content.decode())

    def test_error_pages_hide_webhook_settings(self):
        from django.views.debug import get_default_exception_reporter_filter

        with override_settings(DISCORD_WEBHOOK_URL=HOOK):
            shown = get_default_exception_reporter_filter().get_safe_settings()
        self.assertNotIn("first-secret-token", json.dumps(shown, default=str))
