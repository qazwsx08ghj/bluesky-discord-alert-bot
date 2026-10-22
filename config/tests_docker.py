"""The bot in real Docker containers, driven by the real Windows scripts, and the React panel
in a real browser against those containers.

Opt-in, because it builds an image and starts containers (about 3-5 minutes):

    set RUN_DOCKER_TESTS=1
    venv\\Scripts\\python manage.py test config.tests_docker

Isolation from the real install:
- It runs in a temporary copy of the project (only files Git knows about, so no .env,
  database or backups), with its own Docker project name, volume, image tag and port.
- The "earlier install" it imports has fake credentials and a paused account, and no
  account is ever active, so nothing contacts Bluesky or Discord.
- Cleanup removes only the test project's containers, volume and image; the project
  name is checked to start with "alertbot-test-" first.
"""
import http.cookiejar
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)
FAKE_PASSWORD = "fake-pass-word-for-tests"
FAKE_WEBHOOK = "https://discord.com/api/webhooks/1/fake-test-token"


def docker_available():
    if os.environ.get("RUN_DOCKER_TESTS") != "1" or shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, timeout=60).returncode == 0


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(sys.platform == "win32", "uses the Windows scripts")
@unittest.skipUnless(docker_available(), "set RUN_DOCKER_TESTS=1 with Docker Desktop running")
class DockerDeploymentTests(SimpleTestCase):
    """The tests run in order (test_1 ... test_9) on one test install."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name) / "bot folder"
        files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT,
                               capture_output=True, check=True).stdout.decode().split("\0")
        for name in filter(None, files):
            if (ROOT / name).is_file():
                (cls.dir / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, cls.dir / name)
        assert not (cls.dir / ".env").exists() and not (cls.dir / "db.sqlite3").exists()

        cls.project = f"alertbot-test-{uuid.uuid4().hex[:8]}"
        cls.port = free_port()
        cls.url = f"http://127.0.0.1:{cls.port}"
        cls.env = {k: v for k, v in os.environ.items() if not k.startswith(("ALERTBOT_", "FAKE_"))}
        cls.env.update(ALERTBOT_PROJECT=cls.project, ALERTBOT_PORT=str(cls.port),
                       ALERTBOT_LOCAL_IMAGE="bluesky-alert-bot:test", ALERTBOT_NO_BROWSER="1", ALERTBOT_NO_PAUSE="1",
                       ALERTBOT_ASSUME_YES="1", ALERTBOT_NO_DESKTOP_START="1")
        cls.compose = ["docker", "compose", "-p", cls.project, "--env-file", "docker/compose.env",
                       "-f", "docker-compose.yml", "-f", "docker-compose.build.yml"]
        cls.make_old_install()

    @classmethod
    def tearDownClass(cls):
        assert cls.project.startswith("alertbot-test-")
        subprocess.run([*cls.compose, "down", "--volumes", "--remove-orphans"], cwd=cls.dir, env=cls.env,
                       capture_output=True, timeout=300)
        cls.tmp.cleanup()
        super().tearDownClass()

    @classmethod
    def make_old_install(cls):
        """An earlier, non-Docker install in the folder: its database and .env, all fake."""
        env = {k: v for k, v in os.environ.items() if k != "RUN_MAIN"}
        env.update({k: "" for k in ("BLUESKY_HANDLE", "BLUESKY_PASSWORD", "DISCORD_WEBHOOK_URL", "SECRET_KEY")},
                   DATABASE_PATH=str(cls.dir / "db.sqlite3"), SETTINGS_ENV_MIRROR_PATH="", BOT_LOG_LEVEL="CRITICAL")
        for args in (["migrate", "-v0"], ["shell", "-c", "from bluesky_monitor.models import MonitoredAccount; "
                                          "MonitoredAccount.objects.create(handle='old.bsky.social', is_active=False)"]):
            subprocess.run([sys.executable, str(ROOT / "manage.py"), *args], cwd=ROOT, env=env, check=True,
                           capture_output=True, timeout=120)
        (cls.dir / ".env").write_text(f"SECRET_KEY=old\nBLUESKY_HANDLE=fake.bsky.social\nBLUESKY_PASSWORD={FAKE_PASSWORD}\n"
                                      f"DISCORD_WEBHOOK_URL={FAKE_WEBHOOK}\nPOLL_INTERVAL_MINUTES=5\n", encoding="utf-8")

    # -- helpers ----------------------------------------------------------------------------------
    def script(self, name, **extra):
        res = subprocess.run(["cmd", "/d", "/c", str(self.dir / name)], cwd=self.dir, env={**self.env, **extra},
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="ascii",
                             errors="replace", timeout=900)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return res.stdout

    def dc(self, *args, check=True):
        res = subprocess.run([*self.compose, *args], cwd=self.dir, env=self.env, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=600)
        if check:
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return res.stdout.strip()

    def request(self, method, path, body=None, headers=None, raw=None, ctype="application/json", jar=None):
        jar = jar if jar is not None else self.jar
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers or {})
        if data is not None:
            req.add_header("Content-Type", ctype)
        try:
            res = opener.open(req, timeout=30)
            status, text = res.status, res.read().decode()
        except urllib.error.HTTPError as e:
            status, text = e.code, e.read().decode()
        try:
            return status, json.loads(text)
        except ValueError:
            return status, text

    def wait_until(self, check, seconds=120, what="condition"):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                if check():
                    return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(2)
        self.fail(f"timed out waiting for {what}")

    def healthy(self):
        return self.request("GET", "/api/health/")[0] == 200

    def token_headers(self):
        self.jar = http.cookiejar.CookieJar()
        status, body = self.request("GET", "/api/csrf/")
        self.assertEqual(status, 200)
        return {"X-CSRFToken": body["csrf_token"], "Origin": self.url, "Sec-Fetch-Site": "same-origin"}

    def handles(self):
        status, body = self.request("GET", "/api/accounts/", jar=http.cookiejar.CookieJar())
        self.assertEqual(status, 200)
        return sorted(a["handle"] for a in body["results"])

    def container(self, service):
        return self.dc("ps", "-a", "-q", service)

    def inspect(self, service, fmt):
        return subprocess.run(["docker", "inspect", "-f", fmt, self.container(service)], capture_output=True,
                              text=True, timeout=60).stdout.strip()

    def backups(self):
        return self.dc("run", "--rm", "--no-deps", "web", "ls", "/data/backups").split()

    jar = http.cookiejar.CookieJar()

    # -- the tests, in order ----------------------------------------------------------------------
    def test_1_setup_imports_the_old_install_and_starts_everything(self):
        out = self.script("setup.bat")
        self.assertIn("Setup is complete", out)
        self.assertTrue(self.healthy())
        self.assertEqual(self.handles(), ["old.bsky.social"], "the earlier install's account was imported")
        status, cfg = self.request("GET", "/api/settings/")
        self.assertEqual((cfg["bluesky_handle"], cfg["has_bluesky_password"], cfg["default_channel"]["name"]),
                         ("fake.bsky.social", True, "Default channel"))
        self.assertNotIn(FAKE_PASSWORD, json.dumps(cfg))
        self.assertNotIn("fake-test-token", json.dumps(cfg))
        for service in ("web", "checker"):
            self.assertEqual(self.inspect(service, "{{.State.Status}}"), "running", service)
            self.assertEqual(self.inspect(service, "{{.HostConfig.RestartPolicy.Name}}"), "unless-stopped", service)
            self.assertEqual(self.inspect(service, "{{.Config.User}}"), "bot")
        self.wait_until(lambda: self.request("GET", "/api/status/")[1]["scheduler"]["state"] in ("running", "checking"),
                        what="the checker's heartbeat")

    def test_2_panel_listens_on_this_computer_only_and_debug_is_off(self):
        self.assertEqual(self.dc("port", "web", "8000"), f"127.0.0.1:{self.port}")
        status, page = self.request("GET", "/no-such-page/")
        self.assertEqual(status, 404)
        self.assertNotIn("URLconf", str(page), "no debug page")
        logs = self.dc("logs", "--no-color")
        self.assertNotIn(FAKE_PASSWORD, logs)
        self.assertNotIn("fake-test-token", logs)

    def test_3_host_origin_and_csrf_protection_work_in_docker(self):
        self.assertEqual(self.request("GET", "/api/settings/", headers={"Host": "evil.example"})[0], 400)
        status, body = self.request("POST", "/api/accounts/", {"handle": "x.bsky.social", "is_active": False})
        self.assertEqual((status, body["code"]), (403, "csrf_failed"))
        headers = self.token_headers()
        status, body = self.request("POST", "/api/accounts/", {"handle": "x.bsky.social", "is_active": False},
                                    headers={**headers, "Origin": "http://evil.example"})
        self.assertEqual((status, body["code"]), (403, "cross_site_request"))
        status, body = self.request("POST", "/api/settings/", raw=b"poll_interval_minutes=9", headers=headers,
                                    ctype="application/x-www-form-urlencoded")
        self.assertEqual(status, 415)
        status, body = self.request("POST", "/api/accounts/", {"handle": "new.bsky.social", "is_active": False},
                                    headers=headers)
        self.assertEqual(status, 201, body)
        status, body = self.request("POST", "/api/webhooks/", {"name": "#art", "url": FAKE_WEBHOOK + "2"},
                                    headers=headers)
        self.assertEqual(status, 201, body)
        self.assertEqual(self.request("GET", "/admin/")[0], 404)
        self.assertEqual(self.handles(), ["new.bsky.social", "old.bsky.social"])

    def test_3b_the_react_panel_is_served_and_works_in_a_browser(self):
        """The image contains the built panel, served by Django with its security headers."""
        with urllib.request.urlopen(self.url + "/accounts", timeout=30) as res:
            html, headers = res.read().decode(), res.headers
        self.assertEqual(headers["X-Alertbot-Panel"], "react")
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertIn("csrftoken=", headers.get("Set-Cookie", ""))
        script = re.search(r'src="(/assets/index-[\w-]+\.js)"', html).group(1)
        with urllib.request.urlopen(self.url + script, timeout=30) as res:
            self.assertEqual(res.headers["Content-Type"], "text/javascript; charset=utf-8")
            self.assertIn("immutable", res.headers["Cache-Control"])
        for path in ("/assets/../manage.py", "/assets/db.sqlite3", "/frontend/package.json", "/static/x"):
            self.assertEqual(self.request("GET", path)[0], 404, path)
        self.assertEqual(self.request("GET", "/classic/")[0], 404, "the old page is gone")
        with urllib.request.urlopen(self.url + "/third-party-notices.txt", timeout=30) as res:
            self.assertEqual(res.headers["Content-Type"], "text/plain; charset=utf-8")
            self.assertEqual(res.read(), (ROOT / "THIRD_PARTY_NOTICES.txt").read_bytes(), "the panel ships the notices")

        # The same browser checks as frontend/e2e/docker.spec.ts, in Chromium, against these containers.
        npx = shutil.which("npx")
        if npx is None or not (ROOT / "frontend" / "node_modules" / "@playwright").is_dir():
            self.skipTest("browser part skipped: run `npm ci` and `npx playwright install chromium` in frontend/")
        env = {**os.environ, "E2E_BASE_URL": self.url, "E2E_SECRETS": f"{FAKE_PASSWORD},fake-test-token"}
        res = subprocess.run([npx, "playwright", "test", "--project=panel", "--grep", "@docker", "--reporter=line"],
                             cwd=ROOT / "frontend", env=env, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=600)
        self.assertEqual(res.returncode, 0, res.stdout[-4000:] + res.stderr[-2000:])

    def test_4_crashed_services_come_back_by_themselves(self):
        for service in ("checker", "web"):
            before = int(self.inspect(service, "{{.RestartCount}}") or 0)
            self.dc("exec", "-T", service, "python", "-c", "import os, signal; os.kill(1, signal.SIGTERM)", check=False)
            self.wait_until(lambda: int(self.inspect(service, "{{.RestartCount}}") or 0) > before
                            and self.inspect(service, "{{.State.Status}}") == "running", what=f"{service} restart")
        self.wait_until(self.healthy, what="the panel after the restart")
        self.assertEqual(self.handles(), ["new.bsky.social", "old.bsky.social"])

    def test_5_restart_rebuild_and_recreate_keep_all_data(self):
        self.dc("restart")
        self.wait_until(self.healthy, what="the panel after a restart")
        self.assertEqual(self.handles(), ["new.bsky.social", "old.bsky.social"])
        old_id = self.container("web")
        self.dc("up", "-d", "--build", "--force-recreate")
        self.assertNotEqual(self.container("web"), old_id, "new containers")
        self.wait_until(self.healthy, what="the panel after a rebuild")
        self.assertEqual(self.handles(), ["new.bsky.social", "old.bsky.social"])
        self.assertEqual(self.request("GET", "/api/webhooks/")[1]["count"], 2)

    def test_6_update_backs_up_first_and_keeps_all_data(self):
        old_id = self.container("web")
        # A new version: a changed file makes the rebuilt image differ, like a real update.
        with open(self.dir / "config" / "__init__.py", "a", encoding="utf-8") as f:
            f.write("# new version" + chr(10))
        out = self.script("update.bat")
        self.assertIn("The update is complete", out)
        exported = list((self.dir / "backups").glob("db-before-update-*.sqlite3"))
        self.assertEqual(len(exported), 1, "a copy of the pre-update backup is in the Windows folder")
        self.assertTrue(any(b.startswith("db-before-update-") for b in self.backups()))
        self.assertNotEqual(self.container("web"), old_id, "the update recreated the containers")
        image = subprocess.run(["docker", "image", "inspect", "-f", "{{.Id}}", "bluesky-alert-bot:test"],
                               capture_output=True, text=True, timeout=60).stdout.strip()
        self.assertEqual(self.inspect("web", "{{.Image}}"), image, "the bot runs the newly built version")
        self.assertEqual(self.inspect("checker", "{{.Image}}"), image)
        self.assertEqual(self.handles(), ["new.bsky.social", "old.bsky.social"])

    def test_7_a_database_update_on_start_is_backed_up_first(self):
        self.dc("stop")
        self.dc("run", "--rm", "--no-deps", "web", "python", "manage.py", "migrate", "bluesky_monitor", "0005")
        before = [b for b in self.backups() if b.startswith("db-auto-")]
        out = self.script("start.bat")
        self.assertIn("The control panel is ready", out)
        after = [b for b in self.backups() if b.startswith("db-auto-")]
        self.assertEqual(len(after), len(before) + 1, "one backup before the migration")
        self.assertEqual(self.request("GET", "/api/health/")[1]["migrations_pending"], 0)
        self.assertEqual(self.handles(), ["new.bsky.social", "old.bsky.social"])

    def test_8_containers_removed_and_recreated_keep_the_volume(self):
        self.dc("down")  # containers only: the volume stays (the scripts never do even this)
        self.assertIn(f"{self.project}_bot-data", subprocess.run(["docker", "volume", "ls", "-q"], capture_output=True,
                                                                  text=True).stdout.split())
        self.script("start.bat")
        self.assertEqual(self.handles(), ["new.bsky.social", "old.bsky.social"])
        self.assertEqual(self.request("GET", "/api/settings/")[1]["bluesky_handle"], "fake.bsky.social")

    def test_9_stop_stops_without_deleting(self):
        out = self.script("stop.bat")
        self.assertIn("The bot is stopped", out)
        self.assertEqual(self.dc("ps", "-q"), "")
        self.assertIn(f"{self.project}_bot-data", subprocess.run(["docker", "volume", "ls", "-q"], capture_output=True,
                                                                  text=True).stdout.split())

    def restore_number(self, prefix):
        """The number restore.bat shows for the newest backup starting with prefix (newest first)."""
        self.dc("run", "--rm", "--no-deps", "-v", f"{self.dir / 'backups'}:/export", "web", "backups")
        files = sorted((self.dir / "backups").glob("*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
        return next(i for i, f in enumerate(files, start=1) if f.name.startswith(prefix))

    def test_9b_restore_puts_a_backup_back_and_can_itself_be_undone(self):
        self.script("start.bat")
        headers = self.token_headers()
        status, _ = self.request("POST", "/api/accounts/", {"handle": "after-backup.bsky.social", "is_active": False},
                                 headers=headers)
        self.assertEqual(status, 201)
        self.assertEqual(self.handles(), ["after-backup.bsky.social", "new.bsky.social", "old.bsky.social"])

        out = self.script("restore.bat", ALERTBOT_RESTORE_CHOICE=str(self.restore_number("db-before-update-")))
        self.assertIn("Done. The bot is running with the data from db-before-update-", out)
        self.assertEqual(self.handles(), ["new.bsky.social", "old.bsky.social"], "the backup's accounts")
        self.assertEqual(self.request("GET", "/api/settings/")[1]["bluesky_handle"], "fake.bsky.social")

        out = self.script("restore.bat", ALERTBOT_RESTORE_CHOICE=str(self.restore_number("db-before-restore-")))
        self.assertIn("data from db-before-restore-", out)
        self.assertEqual(self.handles(), ["after-backup.bsky.social", "new.bsky.social", "old.bsky.social"],
                         "the restore was undone with the backup it made first")
        self.wait_until(lambda: self.request("GET", "/api/status/")[1]["scheduler"]["state"] in ("running", "checking"),
                        what="the checker after the restore")
