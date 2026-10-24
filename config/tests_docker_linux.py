"""./alertbot for real: an Ubuntu 24.04 container with Ubuntu's own docker.io and docker-compose-v2
packages runs the Linux download against real Docker. Opt-in, slow (about 15-25 min):

    set RUN_DOCKER_TESTS=1
    venv\\Scripts\\python manage.py test config.tests_docker_linux

The Ubuntu container uses the Docker engine through its socket and shares the engine's network
(--network host), so ports, port conflicts and LAN addresses are the engine's own. Images built for
the tests only (ALERTBOT_TEST_BUILD=1, never published):
  1.0.0-rc.90  built from this checkout; the install starts on it
  1.0.0-rc.91  the same, with a marker in the Linux README.txt
  1.0.0-rc.92  its self-test fails: the trial run fails and ./alertbot update must go back
A fake GitHub on a private test network lists the releases. The download is served from inside the
Ubuntu container, and installed with the exact command from docs/UBUNTU.md.

Isolation: its own project, volume, network, images and containers, all named "alertbot-test-";
cleanup removes all of it. Nothing reaches the bot this computer runs.
"""
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from .tests_docker_updater import GH_SERVER, REPO, docker_available, run

sys.path.insert(0, str(Path(settings.BASE_DIR) / "docker"))
import make_release_bundle as mrb  # noqa: E402

ROOT = Path(settings.BASE_DIR)
UBUNTU = """FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \\
      docker.io docker-compose-v2 iproute2 curl ca-certificates python3 \\
    && rm -rf /var/lib/apt/lists/*
WORKDIR /root
"""
# bundle.json and <platform>.sha256 written again after a file was changed (one line: a Dockerfile RUN).
REINDEX = ("python -c \"import json, sys; from pathlib import Path; sys.path.insert(0, '/app/docker'); "
           "import make_release_bundle as m; "
           "infos = {p: m.index({f.relative_to(Path('/app/bundle', p)).as_posix(): f.read_bytes() "
           "for f in Path('/app/bundle', p).rglob('*') if f.is_file() and f.name != 'bundle.json'}, p) "
           "for p in ('windows', 'linux')}; "
           "[Path('/app/bundle', p, 'bundle.json').write_text(json.dumps(i, indent=2, sort_keys=True) + chr(10)) "
           "for p, i in infos.items()]; "
           "[Path('/app/bundle', p + '.sha256').write_text(''.join(h + ' ' + n + chr(10) "
           "for n, h in sorted(i['files'].items()))) for p, i in infos.items()]\"")
VERSION = "1.0.0-rc.90"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(docker_available(), "set RUN_DOCKER_TESTS=1 with Docker running")
class UbuntuInstallTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.name = f"alertbot-test-lnx-{uuid.uuid4().hex[:6]}"
        cls.tmp = Path(tempfile.mkdtemp())
        cls.net, cls.gh, cls.box = f"{cls.name}-net", f"{cls.name}-gh", f"{cls.name}-ubuntu"
        cls.volume = f"{cls.name}_bot-data"
        try:
            cls._setup()
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def _setup(cls):
        img = cls.name
        run("docker", "build", "-q", "--build-arg", "ALERTBOT_TEST_BUILD=1", "--build-arg", f"ALERTBOT_VERSION={VERSION}",
            "-t", f"{img}:a", str(ROOT), timeout=3600)
        derived = {
            "b": ("1.0.0-rc.91", "RUN printf 'TEST VERSION 1.0.0-rc.91\\n' >> /app/bundle/linux/README.txt && " + REINDEX),
            "c": ("1.0.0-rc.92", "RUN printf 'from django.core.management.base import BaseCommand, CommandError\\n"
                                 "class Command(BaseCommand):\\n    def handle(self, *a, **k):\\n"
                                 "        raise CommandError(\"broken on purpose\")\\n' "
                                 "> /app/botconfig/management/commands/update_selftest.py"),
        }
        for tag, (version, step) in derived.items():
            base = "a" if tag == "b" else "b"
            dockerfile = (f"FROM {img}:{base}\nUSER root\n{step}\nENV ALERTBOT_VERSION={version}\n"
                          f"LABEL org.opencontainers.image.version={version}\nUSER bot\n")
            run("docker", "build", "-q", "-t", f"{img}:{tag}", "-", input=dockerfile, timeout=900)
        run("docker", "build", "-q", "-t", f"{img}:ubuntu", "-", input=UBUNTU, timeout=1800)
        run("docker", "network", "create", cls.net)
        # The release download, made like the release workflow makes it: from the image's own files.
        run("docker", "create", "--name", f"{img}-files", f"{img}:a")
        try:
            run("docker", "cp", f"{img}-files:/app/bundle/linux", str(cls.tmp))
        finally:
            run("docker", "rm", "-f", f"{img}-files", ok=False)
        cls.tarball = mrb.make(f"{img}:a", cls.tmp / "dist", from_bundle=cls.tmp / "linux", platform="linux")
        cls.tarball = cls.tarball.rename(cls.tarball.with_name(f"BlueskyDiscordAlertBot-{VERSION}-linux.tar.gz"))
        run("docker", "run", "-d", "--name", cls.box, "--network", "host", "-v", "/var/run/docker.sock:/var/run/docker.sock",
            f"{img}:ubuntu", "sleep", "infinity")
        run("docker", "exec", cls.box, "mkdir", "-p", "/srv/dl")
        run("docker", "cp", str(cls.tarball), f"{cls.box}:/srv/dl/")
        cls.sh("cd /srv/dl && sha256sum *.tar.gz > SHA256SUMS")
        cls.dl_port = free_port()
        run("docker", "exec", "-d", "-w", "/srv/dl", cls.box, "python3", "-m", "http.server", str(cls.dl_port),
            "--bind", "127.0.0.1")
        cls.lan_ip = cls.sh("ip -o -4 route get 1.1.1.1 | sed -n 's/.* src \\([0-9.]*\\).*/\\1/p'").stdout.strip()
        cls.port = free_port()

    @classmethod
    def tearDownClass(cls):
        assert cls.name.startswith("alertbot-test-")
        subprocess.run(["docker", "exec", cls.box, "sh", "-c",
                        f"cd /root/bluesky-alert-bot 2>/dev/null && ALERTBOT_PROJECT={cls.name} docker compose -p {cls.name} "
                        "--env-file docker/compose.env -f docker-compose.yml down --remove-orphans"],
                       capture_output=True, timeout=300)
        for c in (cls.box, cls.gh, f"{cls.name}-update-lock", f"{cls.name}-update-files", f"{cls.name}-web-1",
                  f"{cls.name}-checker-1", f"{cls.name}-files"):
            subprocess.run(["docker", "rm", "-f", c], capture_output=True, timeout=120)
        subprocess.run(["docker", "volume", "rm", "-f", cls.volume], capture_output=True, timeout=120)
        subprocess.run(["docker", "network", "rm", cls.net, f"{cls.name}_default"], capture_output=True, timeout=120)
        for tag in ("a", "b", "c", "ubuntu"):
            subprocess.run(["docker", "image", "rm", "-f", f"{cls.name}:{tag}"], capture_output=True, timeout=120)
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    # -- helpers --------------------------------------------------------------------------------------
    @classmethod
    def sh(cls, script, stdin="", ok=True, timeout=1800):
        return run("docker", "exec", "-i", "-e", f"ALERTBOT_PROJECT={cls.name}", "-e", "ALERTBOT_CHANNEL=prerelease",
                   "-e", "ALERTBOT_READY_WAIT_SECONDS=150", cls.box, "bash", "-c", script, input=stdin, ok=ok,
                   timeout=timeout)

    def bot(self, command, stdin="", **env):
        exports = "".join(f"export {k}='{v}'; " for k, v in env.items())
        res = self.sh(f"{exports}cd /root/bluesky-alert-bot && ./alertbot {command}", stdin=stdin, ok=False)
        return res.returncode, res.stdout + res.stderr

    def env(self):
        text = self.sh("cat /root/bluesky-alert-bot/docker/compose.env").stdout
        return dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#"))

    def health(self, host="127.0.0.1", port=None, host_header=None):
        header = f"-H 'Host: {host_header}' " if host_header else ""
        res = self.sh(f"curl -s -o /dev/null -w '%{{http_code}}' {header}http://{host}:{port or self.port}/api/health/",
                      ok=False)
        return res.stdout.strip()

    def manage(self, code):
        return run("docker", "run", "--rm", "--network", "none", "-v", f"{self.volume}:/data",
                   "-e", "DATABASE_PATH=/data/db.sqlite3", "-e", "SECRET_KEY_FILE=/data/secret_key",
                   "-e", "BACKUP_DIR=/data/backups", f"{self.name}:a", "python", "manage.py", "shell", "-c",
                   code).stdout.strip().splitlines()[-1]

    def handles(self):
        return self.manage("from bluesky_monitor.models import MonitoredAccount; "
                           "print(sorted(MonitoredAccount.objects.values_list('handle', flat=True)))")

    def add_account(self, handle):
        self.manage("from bluesky_monitor.models import MonitoredAccount; "
                    f"MonitoredAccount.objects.create(handle='{handle}', is_active=False); print('ok')")

    def fake_github(self, versions, withdrawn=(), revision=1):
        from release_meta import MANIFEST_ASSET
        from release_meta.tests import make_manifest

        base = f"http://{self.gh}:8080"
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
        run("docker", "rm", "-f", self.gh, ok=False)
        run("docker", "run", "-d", "--name", self.gh, "--network", self.net, "-e", f"FAKE_GH={json.dumps(routes)}",
            "--entrypoint", "python", f"{self.name}:a", "-c", GH_SERVER)
        time.sleep(2)

    def update(self, test_image=None, stdin=""):
        env = {"ALERTBOT_ASSUME_YES": "1", "ALERTBOT_UPDATE_TEST_NETWORK": self.net,
               "ALERTBOT_UPDATE_TEST_API": f"http://{self.gh}:8080"}
        if test_image:
            env["ALERTBOT_UPDATE_TEST_IMAGE"] = f"{self.name}:{test_image}"
        return self.bot("update", stdin=stdin, **env)

    # -- the tests, in order --------------------------------------------------------------------------
    def test_1_the_documented_install_command(self):
        guide = (ROOT / "docs" / "UBUNTU.md").read_text(encoding="utf-8")
        command = re.search(r"```(?:sh|bash)?\n(V=.*?)\n```", guide, re.S).group(1)
        url = f"https://github.com/{REPO}/releases/download/v$V"
        self.assertIn(url, command)
        local = command.replace(url, f"http://127.0.0.1:{self.dl_port}").replace("1.0.0-rc.4", VERSION, 1)
        self.assertNotEqual(local, command)
        # A tampered download is refused before anything is unpacked or run.
        self.sh("cd /srv/dl && cp *.tar.gz /srv/good.tar.gz && for f in *.tar.gz; do echo tampered >> \"$f\"; done")
        res = self.sh(f"cd /root && {local}", stdin="\n\n", ok=False)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("FAILED", res.stdout + res.stderr)
        self.assertNotIn("Setup is complete", res.stdout)
        self.assertEqual(self.sh("ls /root/bluesky-alert-bot 2>/dev/null | wc -l").stdout.strip(), "0")
        self.sh("cd /srv/dl && for f in *.tar.gz; do cp /srv/good.tar.gz \"$f\"; done && rm -f /root/*.tar.gz /root/SHA256SUMS")
        # The real thing: defaults (Enter, Enter) except the port, which must be free here.
        res = self.sh(f"cd /root && {local}", stdin=f"\n{self.port}\n", ok=False)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("Setup is complete", res.stdout)
        env = self.env()
        self.assertEqual((env["ALERTBOT_BIND"], env["ALERTBOT_PORT"]), ("127.0.0.1", str(self.port)))
        self.assertEqual(self.health(), "200")
        self.assertEqual(self.health(host=self.lan_ip), "000", "not reachable from the network")
        self.add_account("kept.bsky.social")

    def test_2_setup_again_keeps_settings_and_data(self):
        code, out = self.bot("setup", stdin="\n\n")
        self.assertEqual(code, 0, out)
        self.assertIn(f"Host port [{self.port}]", out)
        self.assertEqual(self.env()["ALERTBOT_PORT"], str(self.port))
        self.assertEqual(self.handles(), "['kept.bsky.social']")

    def test_3_a_lan_address_and_a_real_port_conflict(self):
        # Docker Desktop also opens a published address on Windows, where the engine's LAN address doesn't
        # exist, so the bot isn't started on it here: the settings and what Compose makes of them are checked.
        # (On a real Ubuntu server the bot listens there; the script tests cover the rest.)
        self.bot("stop")
        taken = free_port()
        self.sh(f"nohup python3 -m http.server {taken} --bind :: > /dev/null 2>&1 & sleep 1")  # IPv6 (and IPv4)
        code, out = self.bot("config", stdin=f"{self.lan_ip}\nyes\n{taken}\nn\n{self.port}\n")
        self.assertEqual(code, 0, out)
        self.assertIn(f"Port {taken} is already in use", out)
        self.assertIn("./alertbot start", out, "a stopped bot isn't started")
        env = self.env()
        self.assertEqual(env["ALERTBOT_BIND"], self.lan_ip)
        self.assertIn(self.lan_ip, env["ALERTBOT_ALLOWED_HOSTS"])
        self.assertEqual(env["ALERTBOT_CSRF_TRUSTED_ORIGINS"], f"http://{self.lan_ip}:{self.port}")
        config = json.loads(self.sh(f"cd /root/bluesky-alert-bot && docker compose -p {self.name} --env-file docker/compose.env "
                                    "-f docker-compose.yml config --format json").stdout)
        web = config["services"]["web"]
        self.assertEqual([(p["host_ip"], p["published"]) for p in web["ports"]], [(self.lan_ip, str(self.port))])
        self.assertIn(self.lan_ip, web["environment"]["ALLOWED_HOSTS"])
        self.assertEqual(web["environment"]["CSRF_TRUSTED_ORIGINS"], f"http://{self.lan_ip}:{self.port}")
        code, out = self.bot("status")
        self.assertIn(f"Listens:  {self.lan_ip}, port {self.port}", out)
        code, out = self.bot("config", stdin=f"127.0.0.1\n{self.port}\n")
        self.assertEqual(code, 0, out)
        code, out = self.bot("start")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.health(), "200")
        self.assertEqual(self.health(host=self.lan_ip), "000", "not reachable from the network")
        self.assertEqual(self.health(host_header="evil.example"), "400", "other host names refused")

    def test_4_backup_and_restore(self):
        code, out = self.bot("backup")
        self.assertEqual(code, 0, out)
        name = re.search(r"Backed up to (\S+?\.sqlite3)", out).group(1)
        self.assertEqual(self.sh(f"cd /root/bluesky-alert-bot/backups && stat -c %a {name}").stdout.strip(), "600")
        self.add_account("later.bsky.social")
        code, out = self.bot("restore", ALERTBOT_RESTORE_CHOICE="1", ALERTBOT_RESTORE_CONFIRM="RESTORE",
                             ALERTBOT_ASSUME_YES="1")
        self.assertEqual(code, 0, out)
        self.assertIn("Done. The bot is running with the data from", out)
        self.assertEqual(self.handles(), "['kept.bsky.social']")
        self.assertEqual(self.health(), "200")
        self.assertEqual(self.env()["ALERTBOT_PORT"], str(self.port), "settings kept")

    def test_5_up_to_date_and_no_network(self):
        before = self.sh(f"docker ps -q --filter name={self.name}-web").stdout
        self.fake_github([VERSION])
        code, out = self.update()
        self.assertEqual(code, 0, out)
        self.assertIn(f"You have the newest version ({VERSION})", out)
        self.assertEqual(self.sh(f"docker ps -q --filter name={self.name}-web").stdout, before, "no restart")
        run("docker", "rm", "-f", self.gh)
        code, out = self.update()
        self.assertEqual(code, 1, out)
        self.assertIn("Couldn't check for updates", out)

    def test_6_an_update_keeps_everything(self):
        self.fake_github([VERSION, "1.0.0-rc.91"])
        code, out = self.update("b")
        self.assertEqual(code, 0, out)
        self.assertIn("The update is complete: 1.0.0-rc.91 is running", out)
        self.assertIn("TEST VERSION 1.0.0-rc.91", self.sh("cat /root/bluesky-alert-bot/README.txt").stdout)
        env = self.env()
        self.assertEqual(env["ALERTBOT_IMAGE"], f"{self.name}:b")
        self.assertEqual((env["ALERTBOT_BIND"], env["ALERTBOT_PORT"]), ("127.0.0.1", str(self.port)))
        self.assertEqual(self.health(), "200")
        self.assertEqual(self.handles(), "['kept.bsky.social']")

    def test_7_a_failed_trial_goes_back(self):
        self.fake_github([VERSION, "1.0.0-rc.91", "1.0.0-rc.92"])
        code, out = self.update("c")
        self.assertEqual(code, 1, out)
        self.assertIn("failed in the trial run", out)
        self.assertIn("the previous version is back", out)
        self.assertEqual(self.env()["ALERTBOT_IMAGE"], f"{self.name}:b")
        self.assertIn("TEST VERSION 1.0.0-rc.91", self.sh("cat /root/bluesky-alert-bot/README.txt").stdout)
        self.assertEqual(self.health(), "200")
        self.assertEqual(self.handles(), "['kept.bsky.social']")

    def test_8_an_interrupted_update_is_undone(self):
        # As if the terminal was closed right after the files were swapped to rc.92.
        backup = self.sh("ls -1t /root/bluesky-alert-bot/backups | grep before-update | head -n1").stdout.strip()
        d = "/root/bluesky-alert-bot"
        self.sh(f"cd {d} && docker compose -p {self.name} --env-file docker/compose.env -f docker-compose.yml stop && "
                "rm -rf docker/update/previous && mkdir -p docker/update/previous/docker && "
                "cp -p alertbot README.txt docker-compose.yml docker/update/previous/ && "
                "cp -p docker/compose.env docker/release.env docker/update/previous/docker/ && "
                "printf 'alertbot\\nREADME.txt\\ndocker-compose.yml\\ndocker/compose.env\\ndocker/release.env\\n' "
                "> docker/update/previous/.files && echo half-installed > README.txt && "
                f"sed -i 's|^ALERTBOT_IMAGE=.*|ALERTBOT_IMAGE={self.name}:c|' docker/compose.env && "
                f"printf 'STATE=SWAPPED\\nOLD_IMAGE={self.name}:b\\nNEW_IMAGE={self.name}:c\\nTARGET_VERSION=1.0.0-rc.92\\n"
                f"BACKUP_FILE={backup}\\nFP_TRIAL=\\nWAS_RUNNING=1\\n' > docker/update/state.txt")
        code, out = self.bot("start")
        self.assertEqual(code, 1, out)
        self.assertIn("Run ./alertbot update first", out)
        code, out = self.update()
        self.assertEqual(code, 1, out)
        self.assertIn("stopped part-way (at SWAPPED)", out)
        self.assertIn("the previous version is back", out)
        self.assertEqual(self.env()["ALERTBOT_IMAGE"], f"{self.name}:b")
        self.assertIn("TEST VERSION 1.0.0-rc.91", self.sh(f"cat {d}/README.txt").stdout)
        self.assertEqual(self.health(), "200")
        self.assertEqual(self.handles(), "['kept.bsky.social']")
        code, out = self.bot("stop")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.health(), "000")
