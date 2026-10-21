"""The optional development stack (dev/docker-compose.yml) in real Docker containers.

Opt-in, because it builds an image and starts containers (about 1-3 minutes):

    set RUN_DOCKER_TESTS=1
    venv\\Scripts\\python manage.py test config.tests_dev_stack

It checks the promises in that file: the demo bot answers on this computer only, has none of
your files or credentials, cannot reach the internet, and "sends" to a fake Discord. It runs
under its own project name (alertbot-test-dev-…) and port, and removes everything it started.
"""
import http.cookiejar
import json
import os
import shutil
import subprocess
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from config.tests_docker import docker_available, free_port

ROOT = Path(settings.BASE_DIR)
VITE = "localhost:5173"  # what `npm run dev` sends on: its Host and Origin pass through the proxy


@unittest.skipUnless(docker_available(), "set RUN_DOCKER_TESTS=1 with Docker running")
class DevStackTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project = f"alertbot-test-dev-{uuid.uuid4().hex[:8]}"
        assert cls.project.startswith("alertbot-test-")
        cls.port = free_port()
        cls.url = f"http://127.0.0.1:{cls.port}"
        cls.env = {**os.environ, "DEMO_PORT": str(cls.port), "DEMO_SCENARIO": "working"}
        cls.compose = ["docker", "compose", "-p", cls.project, "-f", str(ROOT / "dev" / "docker-compose.yml")]
        try:
            res = subprocess.run([*cls.compose, "up", "-d", "--build", "--wait"], env=cls.env,
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1200)
            assert res.returncode == 0, res.stdout[-3000:] + res.stderr[-3000:]
            cls.compose_output = res.stdout + res.stderr
        except BaseException:
            cls.down()
            raise
        cls.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    @classmethod
    def tearDownClass(cls):
        cls.down()
        super().tearDownClass()

    @classmethod
    def down(cls):
        subprocess.run([*cls.compose, "down", "--volumes", "--remove-orphans"], env=cls.env,
                       capture_output=True, timeout=600)

    # -- helpers ----------------------------------------------------------------------------------
    def request(self, method, path, body=None, headers=None):
        req = urllib.request.Request(self.url + path, method=method, headers=headers or {},
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with self.opener.open(req, timeout=30) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def api(self, method, path, body=None, headers=None):
        status, raw = self.request(method, path, body, headers)
        return status, json.loads(raw or b"null")

    def as_vite(self):
        """Headers of a change sent through the Vite dev server's proxy."""
        token = self.api("GET", "/api/csrf/", headers={"Host": VITE})[1]["csrf_token"]
        return {"Host": VITE, "Origin": f"http://{VITE}", "X-CSRFToken": token, "Content-Type": "application/json"}

    def exec_demo(self, code):
        """Run Python in the demo container: a new process, without the demo server's own network block."""
        return subprocess.run([*self.compose, "exec", "-T", "demo", "python", "-c", code], env=self.env,
                              capture_output=True, text=True, timeout=120)

    def inspect(self, service):
        cid = subprocess.run([*self.compose, "ps", "-q", service], env=self.env, capture_output=True,
                             text=True, check=True).stdout.strip()
        return json.loads(subprocess.run(["docker", "inspect", cid], capture_output=True, text=True,
                                         check=True).stdout)[0]

    # -- tests ------------------------------------------------------------------------------------
    def test_never_reads_the_main_folders_env(self):
        self.assertNotIn("variable is not set", self.compose_output)

    def test_panel_and_api_answer_on_this_computer_only(self):
        status, body = self.api("GET", "/api/health/")
        self.assertEqual((status, body["status"]), (200, "ok"))
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="root"', page)
        ports = self.inspect("gateway")["NetworkSettings"]["Ports"]["8765/tcp"]
        self.assertEqual({p["HostIp"] for p in ports}, {"127.0.0.1"})
        self.assertIsNone(self.inspect("demo")["NetworkSettings"]["Ports"].get("8765/tcp"),
                          "only the gateway publishes a port")

    def test_no_files_volumes_or_credentials_of_yours(self):
        for service in ("demo", "gateway"):
            info = self.inspect(service)
            self.assertEqual([m for m in info["Mounts"] if m["Type"] in ("bind", "volume")], [], service)
            names = {e.split("=", 1)[0] for e in info["Config"]["Env"]}
            for key in ("BLUESKY_HANDLE", "BLUESKY_PASSWORD", "DISCORD_WEBHOOK_URL", "TEST_DISCORD_WEBHOOK_URL",
                        "DATABASE_PATH", "SECRET_KEY_FILE", "LEGACY_ENV_FILE"):
                self.assertNotIn(key, names, f"{service}: {key}")
            self.assertTrue(info["HostConfig"]["ReadonlyRootfs"], service)
        res = self.exec_demo("import pathlib; print([p for p in ('/app/.env', '/app/db.sqlite3', '/app/backups') "
                             "if pathlib.Path(p).exists()] + list(pathlib.Path('/data').iterdir()))")  # /data: empty
        self.assertEqual(res.stdout.strip(), "[]", res.stderr)
        status, body = self.api("GET", "/api/settings/")
        self.assertEqual(body["bluesky_handle"], "alertbot-demo.bsky.social", "the made-up demo login")
        self.assertEqual(self.api("GET", "/api/health/")[1]["version"], "demo")

    def test_the_demo_container_has_no_internet(self):
        res = self.exec_demo(
            "import socket\n"
            "out = []\n"
            "for host in ('bsky.social', 'public.api.bsky.app', 'discord.com'):\n"
            "    try:\n"
            "        socket.getaddrinfo(host, 443); out.append('resolved ' + host)\n"
            "    except OSError:\n"
            "        pass\n"
            "for ip in ('1.1.1.1', '8.8.8.8'):\n"
            "    try:\n"
            "        socket.create_connection((ip, 443), timeout=5).close(); out.append('connected ' + ip)\n"
            "    except OSError:\n"
            "        pass\n"
            "print(out)\n")
        self.assertEqual(res.stdout.strip(), "[]", res.stderr)
        networks = self.inspect("demo")["NetworkSettings"]["Networks"]
        self.assertEqual(list(networks), [f"{self.project}_no-internet"])

    def test_changes_through_the_vite_proxy_work_and_discord_is_fake(self):
        status, channels = self.api("GET", "/api/webhooks/")
        self.assertEqual(status, 200)
        channel = channels["results"][0]
        before = len(self.api("GET", "/__demo__/discord/")[1]["messages"])
        status, body = self.api("POST", f"/api/webhooks/{channel['id']}/test/", headers=self.as_vite())
        self.assertEqual((status, body), (200, {"status": "sent"}))
        self.assertEqual(len(self.api("GET", "/__demo__/discord/")[1]["messages"]), before + 1,
                         "recorded by the fake Discord, never sent")
        status, body = self.api("POST", "/api/accounts/", {"handle": "x.bsky.social", "is_active": False},
                                headers={**self.as_vite(), "Origin": "http://evil.example"})
        self.assertEqual((status, body["code"]), (403, "cross_site_request"))

    def test_scripts_and_release_never_use_it(self):
        for path in [*ROOT.glob("*.bat"), *(ROOT / "docker").rglob("*"), ROOT / "docker-compose.yml"]:
            if path.is_file():
                self.assertNotIn("dev/docker-compose", path.read_text(encoding="utf-8", errors="replace"), path)
        self.assertTrue(shutil.which("docker"))
