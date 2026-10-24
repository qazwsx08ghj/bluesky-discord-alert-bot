"""The one-click updater for real: update.bat driving real containers. Opt-in, slow (about 10-20 min):

    set RUN_DOCKER_TESTS=1
    venv\\Scripts\\python manage.py test config.tests_docker_updater

Versions (images built for the tests only, ALERTBOT_TEST_BUILD=1, never published):
  1.0.0-rc.90  built from this checkout; the install starts on it
  1.0.0-rc.91  the same, with a marker in its README.txt (so the files' swap is visible)
  1.0.0-rc.92  its self-test fails: the trial run fails, and update.bat must go back
  1.0.0-rc.93  its web service can't start: update.bat must notice and go back
A fake GitHub (a container on a private test network) lists the releases and release/withdrawn.json.
Pulling from ghcr.io is replaced by these local images (ALERTBOT_UPDATE_TEST_IMAGE); everything
else is the real update.bat, the real images and a real data volume.

Isolation: its own project, volume, network, images, port and temporary folder, all named
"alertbot-test-"; nothing reaches the internet; cleanup removes all of it.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import uuid
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)
REPO = "qazwsx08ghj/bluesky-discord-alert-bot"
REINDEX = ("python -c \"import sys, json; sys.path.insert(0, '/app/docker'); import make_release_bundle as m; "
           "from pathlib import Path; d = Path('/app/bundle/windows'); "
           "files = {p.relative_to(d).as_posix(): p.read_bytes() for p in d.rglob('*') if p.is_file() and p.name != 'bundle.json'}; "
           "info = m.index(files); (d / 'bundle.json').write_text(json.dumps(info, indent=2, sort_keys=True) + chr(10)); "
           "Path('/app/bundle/windows.sha256').write_text(''.join(h + ' ' + n + chr(10) for n, h in sorted(info['files'].items())))\"")
GH_SERVER = r'''
import json, os
from http.server import BaseHTTPRequestHandler, HTTPServer
routes = json.loads(os.environ["FAKE_GH"])
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = routes.get(self.path)
        if body is None:
            self.send_response(404); self.end_headers(); return
        data = body.encode()
        self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)
    def log_message(self, *args):
        pass
HTTPServer(("0.0.0.0", 8080), H).serve_forever()
'''


def docker_available():
    if os.environ.get("RUN_DOCKER_TESTS") != "1" or shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, timeout=60).returncode == 0


def run(*args, ok=True, timeout=900, **kw):
    res = subprocess.run(list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=timeout, **kw)
    if ok and res.returncode != 0:
        raise AssertionError(f"{' '.join(map(str, args[:5]))} failed:\n{res.stdout[-1500:]}\n{res.stderr[-1500:]}")
    return res


@unittest.skipUnless(sys.platform == "win32", "drives the Windows scripts")
@unittest.skipUnless(docker_available(), "set RUN_DOCKER_TESTS=1 with Docker Desktop running")
class UpdaterInRealContainersTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.name = f"alertbot-test-upd-{uuid.uuid4().hex[:6]}"
        cls.tmp = Path(tempfile.mkdtemp())
        cls.dir = cls.tmp / "bot folder"
        cls.net, cls.gh_name = f"{cls.name}-net", f"{cls.name}-gh"
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        try:
            cls._setup()
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def _setup(cls):
        img = cls.name
        run("docker", "build", "-q", "--build-arg", "ALERTBOT_TEST_BUILD=1", "--build-arg", "ALERTBOT_VERSION=1.0.0-rc.90",
            "-t", f"{img}:a", str(ROOT), timeout=3600)
        derived = {
            "b": ("1.0.0-rc.91", "RUN printf '\\r\\nTEST VERSION 1.0.0-rc.91\\r\\n' >> /app/bundle/windows/README.txt && " + REINDEX),
            "c": ("1.0.0-rc.92", "RUN printf 'from django.core.management.base import BaseCommand, CommandError\\n"
                                 "class Command(BaseCommand):\\n    def handle(self, *a, **k):\\n"
                                 "        raise CommandError(\"broken on purpose\")\\n' "
                                 "> /app/botconfig/management/commands/update_selftest.py"),
            "d": ("1.0.0-rc.93", "RUN printf 'def on_starting(server):\\n    raise SystemExit(\"broken on purpose\")\\n' "
                                 "> /app/config/gunicorn_conf.py"),
        }
        base = {"b": "a", "c": "b", "d": "b"}
        for tag, (version, step) in derived.items():
            dockerfile = (f"FROM {img}:{base[tag]}\nUSER root\n{step}\nENV ALERTBOT_VERSION={version}\n"
                          f"LABEL org.opencontainers.image.version={version}\nUSER bot\n")
            run("docker", "build", "-q", "-t", f"{img}:{tag}", "-", input=dockerfile, timeout=900)
        run("docker", "network", "create", cls.net)
        # The install: rc.90's own deployment files (as from its download), pinned to rc.90.
        cls.dir.mkdir(parents=True)
        run("docker", "create", "--name", f"{img}-files", f"{img}:a")
        try:
            run("docker", "cp", f"{img}-files:/app/bundle/windows/.", str(cls.dir))
        finally:
            run("docker", "rm", "-f", f"{img}-files", ok=False)
        (cls.dir / "bundle.json").unlink()
        (cls.dir / "docker" / "compose.env").write_text(
            f"# test install\nALERTBOT_IMAGE={img}:a\nALERTBOT_PORT={cls.port}\n", encoding="ascii")
        cls.env = {k: v for k, v in os.environ.items() if not k.startswith(("ALERTBOT_", "FAKE_"))}
        cls.env.update(ALERTBOT_PROJECT=cls.name, ALERTBOT_NO_BROWSER="1", ALERTBOT_NO_PAUSE="1", ALERTBOT_ASSUME_YES="1",
                       ALERTBOT_NO_DESKTOP_START="1", ALERTBOT_READY_WAIT_SECONDS="90", ALERTBOT_CHANNEL="prerelease",
                       ALERTBOT_UPDATE_TEST_NETWORK=cls.net, ALERTBOT_UPDATE_TEST_API=f"http://{cls.gh_name}:8080")
        cls.compose = ["docker", "compose", "-p", cls.name, "--env-file", "docker/compose.env", "-f", "docker-compose.yml"]
        run(*cls.compose, "up", "-d", cwd=cls.dir)
        cls.wait_healthy()
        cls.manage_shell("from bluesky_monitor.models import MonitoredAccount; "
                         "MonitoredAccount.objects.create(handle='kept.bsky.social', is_active=False)")

    @classmethod
    def tearDownClass(cls):
        assert cls.name.startswith("alertbot-test-")
        if (cls.dir / "docker-compose.yml").exists():
            subprocess.run([*cls.compose, "down", "--volumes", "--remove-orphans"], cwd=cls.dir, capture_output=True,
                           timeout=300)
        for c in (cls.gh_name, f"{cls.name}-update-lock", f"{cls.name}-update-files"):
            subprocess.run(["docker", "rm", "-f", c], capture_output=True, timeout=120)
        subprocess.run(["docker", "volume", "rm", "-f", f"{cls.name}_bot-data"], capture_output=True, timeout=120)
        subprocess.run(["docker", "network", "rm", cls.net], capture_output=True, timeout=120)
        for tag in "abcd":
            subprocess.run(["docker", "image", "rm", "-f", f"{cls.name}:{tag}"], capture_output=True, timeout=120)
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    # -- helpers --------------------------------------------------------------------------------------
    @classmethod
    def runner(cls, image, *command):
        return run("docker", "run", "--rm", "--network", "none", "-v", f"{cls.name}_bot-data:/data",
                   "-e", "DATABASE_PATH=/data/db.sqlite3", "-e", "SECRET_KEY_FILE=/data/secret_key",
                   "-e", "BACKUP_DIR=/data/backups", image, *command)

    @classmethod
    def manage_shell(cls, code):
        return cls.runner(f"{cls.name}:a", "python", "manage.py", "shell", "-c", code).stdout

    @classmethod
    def health(cls):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/api/health/", timeout=5) as r:
                return json.loads(r.read())
        except (OSError, ValueError):
            return {}

    @classmethod
    def wait_healthy(cls, seconds=180):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if cls.health().get("status") == "ok":
                return
            time.sleep(2)
        raise AssertionError("the bot didn't become healthy")

    def fake_github(self, versions, withdrawn=(), revision=1):
        from release_meta import MANIFEST_ASSET
        from release_meta.tests import make_manifest

        base = f"http://{self.gh_name}:8080"
        routes, releases = {}, []
        for v in versions:
            routes[f"/dl/{v}"] = json.dumps(make_manifest(v))
            releases.append({"tag_name": f"v{v}", "draft": False, "immutable": True, "prerelease": True, "assets": [
                {"name": MANIFEST_ASSET, "size": 900, "digest": None, "browser_download_url": f"{base}/dl/{v}"},
                {"name": f"BlueskyDiscordAlertBot-{v}.zip", "size": 4096, "digest": None,
                 "browser_download_url": f"{base}/dl/zip"}]})
        routes[f"/repos/{REPO}/releases?per_page=100"] = json.dumps(releases)
        routes[f"/repos/{REPO}/contents/release/withdrawn.json?ref=main"] = json.dumps({
            "schema": "alertbot-withdrawn/1", "revision": revision, "updated_at": "2026-12-01",
            "withdrawn": [{"version": v, "withdrawn_at": "2026-12-01", "reason": "Broken test build.", "replacement": None}
                          for v in withdrawn]})
        run("docker", "rm", "-f", self.gh_name, ok=False)
        run("docker", "run", "-d", "--name", self.gh_name, "--network", self.net, "-e", f"FAKE_GH={json.dumps(routes)}",
            "--entrypoint", "python", f"{self.name}:a", "-c", GH_SERVER)
        time.sleep(2)

    def update(self, test_image=None):
        env = dict(self.env)
        if test_image:
            env["ALERTBOT_UPDATE_TEST_IMAGE"] = f"{self.name}:{test_image}"
        res = subprocess.run(["cmd", "/d", "/c", str(self.dir / "update.bat")], cwd=self.dir, env=env,
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="ascii", errors="replace",
                             timeout=1800)
        return res.returncode, res.stdout + res.stderr

    def handles(self):
        return self.manage_shell("from bluesky_monitor.models import MonitoredAccount; "
                                 "print(sorted(MonitoredAccount.objects.values_list('handle', flat=True)))").strip().splitlines()[-1]

    def web_id(self):
        return run(*self.compose, "ps", "-q", "web", cwd=self.dir).stdout.strip()

    def pinned(self):
        env = (self.dir / "docker" / "compose.env").read_text(encoding="ascii")
        self.assertIn(f"ALERTBOT_PORT={self.port}", env, "the local settings are kept")
        return next(l.split("=", 1)[1] for l in env.splitlines() if l.startswith("ALERTBOT_IMAGE="))

    def state(self):
        path = self.dir / "docker" / "update" / "state.txt"
        return dict(l.split("=", 1) for l in path.read_text(encoding="ascii").splitlines()) if path.exists() else {}

    def backups(self):
        return sorted(self.runner(f"{self.name}:a", "sh", "-c", "ls /data/backups").stdout.split())

    # -- the tests, in order --------------------------------------------------------------------------
    def test_1_up_to_date_changes_nothing(self):
        self.fake_github(["1.0.0-rc.90"])
        before = (self.web_id(), self.backups())
        code, out = self.update()
        self.assertEqual(code, 0, out)
        self.assertIn("You have the newest version (1.0.0-rc.90)", out)
        self.assertEqual((self.web_id(), self.backups()), before, "no restart, no backup")

    def test_2_no_network_changes_nothing(self):
        run("docker", "rm", "-f", self.gh_name)
        code, out = self.update()
        self.assertEqual(code, 1, out)
        self.assertIn("Couldn't check for updates", out)
        self.assertEqual((self.health().get("version"), self.pinned()), ("1.0.0-rc.90", f"{self.name}:a"))

    def test_3_an_update_keeps_everything(self):
        self.fake_github(["1.0.0-rc.90", "1.0.0-rc.91"])
        code, out = self.update("b")
        self.assertEqual(code, 0, out)
        self.assertIn("The update is complete: 1.0.0-rc.91 is running", out)
        self.assertEqual(self.health().get("version"), "1.0.0-rc.91")
        self.assertEqual(self.pinned(), f"{self.name}:b")
        self.assertIn(b"TEST VERSION 1.0.0-rc.91", (self.dir / "README.txt").read_bytes(), "the new files")
        self.assertEqual(self.handles(), "['kept.bsky.social']")
        self.assertTrue(any(b.startswith("db-before-update-") for b in self.backups()))
        self.assertEqual(self.state()["STATE"], "DONE")

    def test_4_a_failed_trial_goes_back(self):
        self.fake_github(["1.0.0-rc.90", "1.0.0-rc.91", "1.0.0-rc.92"])
        code, out = self.update("c")
        self.assertEqual(code, 1, out)
        self.assertIn("failed in the trial run", out)
        self.assertIn("the previous version is back", out)
        self.wait_healthy()
        self.assertEqual(self.health().get("version"), "1.0.0-rc.91")
        self.assertEqual(self.pinned(), f"{self.name}:b")
        self.assertIn(b"TEST VERSION 1.0.0-rc.91", (self.dir / "README.txt").read_bytes())
        self.assertEqual(self.handles(), "['kept.bsky.social']")
        self.assertEqual(self.state()["STATE"], "ROLLED_BACK")

    def test_5_a_version_that_doesnt_start_goes_back(self):
        self.fake_github(["1.0.0-rc.91", "1.0.0-rc.92", "1.0.0-rc.93"], withdrawn=["1.0.0-rc.92"], revision=2)
        code, out = self.update("d")
        self.assertEqual(code, 1, out)
        self.assertIn("1.0.0-rc.93 didn't start properly", out)
        self.assertIn("the previous version is back", out)
        self.wait_healthy()
        self.assertEqual(self.health().get("version"), "1.0.0-rc.91")
        self.assertEqual(self.pinned(), f"{self.name}:b")
        self.assertEqual(self.handles(), "['kept.bsky.social']")

    def test_6_withdrawn_versions_arent_offered(self):
        self.fake_github(["1.0.0-rc.91", "1.0.0-rc.92", "1.0.0-rc.93"], withdrawn=["1.0.0-rc.92", "1.0.0-rc.93"],
                         revision=3)
        code, out = self.update()
        self.assertEqual(code, 0, out)
        self.assertIn("You have the newest version (1.0.0-rc.91)", out)

    def test_7_an_update_interrupted_after_the_swap_is_undone(self):
        # As if the window was closed right after the files were swapped to rc.92.
        backup = [b for b in self.backups() if b.startswith("db-before-update-")][-1]
        run(*self.compose, "stop", cwd=self.dir)
        upd = self.dir / "docker" / "update"
        prev = upd / "previous"
        for name in ("README.txt", "update.bat", "docker-compose.yml", "docker/compose.env", "docker/scripts/common.bat"):
            (prev / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.dir / name, prev / name)
        (self.dir / "README.txt").write_bytes(b"half-installed")
        env = self.dir / "docker" / "compose.env"
        env.write_text(env.read_text(encoding="ascii").replace(f"{self.name}:b", f"{self.name}:c"), encoding="ascii")
        (upd / "state.txt").write_text(
            f"STATE=SWAPPED\nOLD_IMAGE={self.name}:b\nNEW_IMAGE={self.name}:c\nTARGET_VERSION=1.0.0-rc.92\n"
            f"BACKUP_FILE={backup}\nFP_TRIAL=\nWAS_RUNNING=1\n", encoding="ascii")
        code, out = self.update()
        self.assertEqual(code, 1, out)
        self.assertIn("An earlier update stopped part-way (at SWAPPED)", out)
        self.assertIn("the previous version is back", out)
        self.wait_healthy()
        self.assertEqual(self.health().get("version"), "1.0.0-rc.91")
        self.assertIn(b"TEST VERSION 1.0.0-rc.91", (self.dir / "README.txt").read_bytes())
        self.assertEqual(self.handles(), "['kept.bsky.social']")

    def test_8_a_second_update_waits_for_the_lock(self):
        run("docker", "create", "--name", f"{self.name}-update-lock", f"{self.name}:a", "true")
        try:
            self.fake_github(["1.0.0-rc.91", "1.0.0-rc.92", "1.0.0-rc.93", "1.0.0-rc.94"],
                             withdrawn=["1.0.0-rc.92", "1.0.0-rc.93"], revision=3)  # the list it accepted
            code, out = self.update("b")
            self.assertEqual(code, 1, out)
            self.assertIn("Another update is running", out, out[-1500:])
            self.assertEqual(self.health().get("version"), "1.0.0-rc.91")
        finally:
            run("docker", "rm", "-f", f"{self.name}-update-lock", ok=False)
