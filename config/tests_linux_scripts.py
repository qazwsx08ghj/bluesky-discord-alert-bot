"""docker/linux/alertbot (Ubuntu) under bash, with a fake docker, ss and ip (config/testing/fake_*.sh).
Each test unpacks the Linux download into a temporary folder, as a user would. The real thing, with
Docker: config/tests_docker_linux.py."""
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

sys.path.insert(0, str(Path(settings.BASE_DIR) / "docker"))
import make_release_bundle as mrb  # noqa: E402

ROOT = Path(settings.BASE_DIR)
TESTING = ROOT / "config" / "testing"
REPO = "ghcr.io/qazwsx08ghj/bluesky-discord-alert-bot"
OLD = f"{REPO}:1.0.0-rc.3@sha256:{'a' * 64}"
NEW = f"{REPO}:1.0.0-rc.4@sha256:{'b' * 64}"


def find_bash():
    if sys.platform == "win32":
        for candidate in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
            if os.path.exists(candidate):
                return candidate
        return None  # never WSL's bash.exe: it can't see this folder the same way
    return shutil.which("bash")


BASH = find_bash()


@unittest.skipUnless(BASH, "needs bash (Git for Windows on Windows)")
class LinuxScriptTestCase(SimpleTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        mrb.make(OLD, self.tmp / "dl", platform="linux")
        with tarfile.open(next((self.tmp / "dl").glob("*.tar.gz"))) as tar:
            tar.extractall(self.tmp)  # made by this test
        self.dir = self.tmp / "bluesky-alert-bot"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for fake, name in (("fake_docker.sh", "docker"), ("fake_ss.sh", "ss"), ("fake_ip.sh", "ip")):
            shutil.copyfile(TESTING / fake, self.bin / name)
            (self.bin / name).chmod(0o755)
        self.log = self.tmp / "docker.log"
        self.bundle = self.tmp / "bundle"

    def run_script(self, *args, stdin="", **fake):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ALERTBOT_", "FAKE_", "FAIL_ON"))}
        # The fake docker by its full path: a real docker on this computer must never be reached.
        env.update(PATH=str(self.bin) + os.pathsep + env.get("PATH", ""), FAKE_DOCKER_LOG=str(self.log),
                   ALERTBOT_DOCKER=(self.bin / "docker").as_posix(), ALERTBOT_READY_WAIT_SECONDS="2",
                   LC_ALL="C.UTF-8")
        env.update({k: str(v) for k, v in fake.items()})
        if self.log.exists():
            self.log.unlink()
        for leftover in self.tmp.glob("docker.log.*"):
            leftover.unlink()
        res = subprocess.run([BASH, "./alertbot", *args], cwd=self.dir, env=env, input=stdin, capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=120)
        calls = self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []
        return res.returncode, res.stdout + res.stderr, calls

    def env(self):
        text = (self.dir / "docker" / "compose.env").read_text(encoding="utf-8")
        return dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#"))

    def write_env(self, **values):
        lines = ["# an install"] + [f"{k}={v}" for k, v in values.items()]
        (self.dir / "docker" / "compose.env").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def installed(self, **extra):
        values = {"ALERTBOT_IMAGE": OLD, "ALERTBOT_BIND": "127.0.0.1", "ALERTBOT_PORT": "8000",
                  "ALERTBOT_ALLOWED_HOSTS": "localhost,127.0.0.1,[::1]", "ALERTBOT_CSRF_TRUSTED_ORIGINS": ""}
        values.update(extra)
        self.write_env(**values)

    def has(self, calls, text):
        return any(text in c for c in calls)

    def write_bundle(self, change=None):
        """The new version's files, as `docker cp` would copy them out of its image."""
        info = mrb.export(self.bundle / "linux", platform="linux")
        if change:
            change(self.bundle / "linux")
            files = {p.relative_to(self.bundle / "linux").as_posix(): p.read_bytes()
                     for p in (self.bundle / "linux").rglob("*") if p.is_file() and p.name != "bundle.json"}
            info = mrb.index(files, "linux")
            (self.bundle / "linux.sha256").write_bytes(
                "".join(f"{h} {n}\n" for n, h in sorted(info["files"].items())).encode())
        return info


class SetupTests(LinuxScriptTestCase):
    def test_a_fresh_setup_with_the_defaults(self):
        self.assertFalse((self.dir / "docker" / "compose.env").exists(), "the download never holds settings")
        code, out, calls = self.run_script("setup", FAKE_NOT_SET_UP=1)
        self.assertEqual(code, 0, out)
        env = self.env()
        self.assertEqual(env["ALERTBOT_IMAGE"], OLD, "from docker/release.env")
        self.assertEqual((env["ALERTBOT_BIND"], env["ALERTBOT_PORT"]), ("127.0.0.1", "8000"))
        self.assertEqual(env["ALERTBOT_ALLOWED_HOSTS"], "localhost,127.0.0.1,[::1]")
        self.assertEqual(env["ALERTBOT_CSRF_TRUSTED_ORIGINS"], "")
        self.assertIn("http://127.0.0.1:8000", out)
        self.assertIn("ssh -L 8000:127.0.0.1:8000", out)
        self.assertTrue(self.has(calls, "compose -p alertbot --env-file docker/compose.env -f docker-compose.yml up -d"))
        self.assertFalse(self.has(calls, "check_compat"), "no data yet")

    def test_running_setup_again_keeps_everything(self):
        self.installed(ALERTBOT_BIND="192.168.1.10", ALERTBOT_PORT="8080", ALERTBOT_ALLOWED_HOSTS="x",
                       ALERTBOT_CSRF_TRUSTED_ORIGINS="y")
        code, out, calls = self.run_script("setup", stdin="\n" * 3 + "yes\n\n", FAKE_RUNNING=1)
        self.assertEqual(code, 0, out)
        self.assertIn("Bind IP [192.168.1.10]", out)
        self.assertIn("Host port [8080]", out, "the saved settings are the defaults")
        env = self.env()
        self.assertEqual((env["ALERTBOT_BIND"], env["ALERTBOT_PORT"]), ("192.168.1.10", "8080"))
        self.assertTrue(self.has(calls, "check_compat"))
        self.assertFalse(self.has(calls, "volume rm") or self.has(calls, " down"), "data is never removed")

    def test_unpacking_another_version_over_an_install_keeps_it(self):
        self.installed(ALERTBOT_IMAGE=NEW)
        code, out, _ = self.run_script("setup", FAKE_RUNNING=1)
        self.assertEqual(code, 0, out)
        self.assertIn("already has the bot installed (1.0.0-rc.4)", out)
        self.assertIn("./alertbot update", out)
        self.assertEqual(self.env()["ALERTBOT_IMAGE"], NEW, "not changed by unpacking a download")

    def test_a_lan_address_and_port(self):
        code, out, _ = self.run_script("setup", stdin="192.168.1.10\nyes\n8080\n", FAKE_NOT_SET_UP=1)
        self.assertEqual(code, 0, out)
        self.assertIn("has NO login", out)
        env = self.env()
        self.assertEqual((env["ALERTBOT_BIND"], env["ALERTBOT_PORT"]), ("192.168.1.10", "8080"))
        self.assertEqual(env["ALERTBOT_ALLOWED_HOSTS"], "localhost,127.0.0.1,[::1],192.168.1.10")
        self.assertEqual(env["ALERTBOT_CSRF_TRUSTED_ORIGINS"], "http://192.168.1.10:8080")
        self.assertIn("http://192.168.1.10:8080", out)

    def test_other_devices_only_after_typing_yes(self):
        # Not confirmed: asked again; with no more answers, the safe default.
        code, out, _ = self.run_script("setup", stdin="192.168.1.10\nno\n", FAKE_NOT_SET_UP=1)
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("Bind IP [127.0.0.1]"), 2)
        self.assertEqual(self.env()["ALERTBOT_BIND"], "127.0.0.1")
        self.assertNotIn("192.168.1.10", self.env()["ALERTBOT_ALLOWED_HOSTS"])
        code, out, _ = self.run_script("setup", stdin="192.168.1.10\nno\n127.0.0.1\n\n", FAKE_NOT_SET_UP=1)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.env()["ALERTBOT_BIND"], "127.0.0.1", "asked again, then the safe choice")

    def test_every_address_and_a_public_address_need_public(self):
        code, out, _ = self.run_script("setup", stdin="0.0.0.0\nyes\n", FAKE_NOT_SET_UP=1,
                                       FAKE_IPS="127.0.0.1 192.168.1.10 203.0.113.7")
        self.assertEqual(self.env()["ALERTBOT_BIND"], "127.0.0.1", "yes isn't enough: the safe default")
        self.assertIn("reachable from the INTERNET", out)
        self.assertIn("past ufw", out)
        code, out, _ = self.run_script("setup", stdin="0.0.0.0\nPUBLIC\n\n", FAKE_NOT_SET_UP=1,
                                       FAKE_IPS="127.0.0.1 192.168.1.10 203.0.113.7")
        self.assertEqual(code, 0, out)
        env = self.env()
        self.assertEqual(env["ALERTBOT_BIND"], "0.0.0.0")
        self.assertEqual(env["ALERTBOT_ALLOWED_HOSTS"], "localhost,127.0.0.1,[::1],192.168.1.10,203.0.113.7")
        self.assertEqual(env["ALERTBOT_CSRF_TRUSTED_ORIGINS"], "http://192.168.1.10:8000,http://203.0.113.7:8000")
        code, out, _ = self.run_script("setup", stdin="0.0.0.0\nyes\n", FAKE_NOT_SET_UP=1, FAKE_IPS="127.0.0.1 10.0.0.5")
        self.assertNotIn("INTERNET", out, "only private addresses: yes is enough")

    def test_invalid_and_foreign_addresses_and_ports_are_asked_again(self):
        stdin = "300.1.1.1\nlocalhost\n10.9.9.9\n127.0.0.1\n0\n99999\nabc\n8001\n"
        code, out, _ = self.run_script("setup", stdin=stdin, FAKE_NOT_SET_UP=1)
        self.assertEqual(code, 0, out)
        self.assertIn('"300.1.1.1" isn\'t an IPv4 address', out)
        self.assertIn("This server has no address 10.9.9.9", out)
        self.assertIn('"99999" isn\'t a port number', out)
        self.assertEqual((self.env()["ALERTBOT_BIND"], self.env()["ALERTBOT_PORT"]), ("127.0.0.1", "8001"))

    def test_a_taken_port_offers_another_one(self):
        code, out, _ = self.run_script("setup", stdin="\n\n\n", FAKE_NOT_SET_UP=1,
                                       FAKE_PORTS_IN_USE="0.0.0.0:8000 [::]:8001")
        self.assertEqual(code, 0, out)
        self.assertIn("Port 8000 is already in use", out)
        self.assertIn("Use port 8002 instead?", out, "8001 is taken on IPv6")
        self.assertEqual(self.env()["ALERTBOT_PORT"], "8002")

    def test_an_ipv6_listener_counts(self):
        code, out, _ = self.run_script("setup", stdin="\n\nn\n", FAKE_NOT_SET_UP=1, FAKE_PORTS_IN_USE="[::1]:8000")
        self.assertEqual(code, 1, out)
        self.assertIn("Port 8000 is already in use", out)

    def test_the_bots_own_port_isnt_a_conflict(self):
        self.installed()
        code, out, _ = self.run_script("setup", FAKE_RUNNING=1, FAKE_PORTS_IN_USE="127.0.0.1:8000")
        self.assertEqual(code, 0, out)
        self.assertNotIn("already in use", out)

    def test_docker_problems_are_explained(self):
        code, out, _ = self.run_script("setup", ALERTBOT_DOCKER="no-such-docker-here")
        self.assertIn("Docker isn't installed", out)
        self.assertIn("https://docs.docker.com/engine/install/ubuntu/", out)
        code, out, _ = self.run_script("setup", FAKE_INFO="denied")
        self.assertIn("sudo usermod -aG docker", out)
        code, out, _ = self.run_script("setup", FAKE_INFO="down")
        self.assertIn("sudo systemctl enable --now docker", out)
        code, out, _ = self.run_script("setup", FAKE_COMPOSE_VERSION="2.20.3")
        self.assertIn("Docker Compose 2.20.3 is too old", out)
        self.assertEqual(code, 1)
        self.assertFalse((self.dir / "docker" / "compose.env").exists())

    def test_newer_data_stops_before_starting(self):
        self.installed()
        code, out, calls = self.run_script("setup", FAKE_COMPAT=3)
        self.assertEqual(code, 1, out)
        self.assertIn("last used by a newer version", out)
        self.assertFalse(self.has(calls, "up -d"))


class OperateTests(LinuxScriptTestCase):
    def setUp(self):
        super().setUp()
        self.installed()

    def test_start_stop_status(self):
        code, out, calls = self.run_script("start")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.has(calls, "up -d"))
        code, out, calls = self.run_script("stop")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.has(calls, "compose -p alertbot --env-file docker/compose.env -f docker-compose.yml stop"))
        code, out, _ = self.run_script("status", FAKE_RUNNING=1)
        self.assertEqual(code, 0, out)
        self.assertIn("Version:  1.0.0-rc.3", out)
        self.assertIn("Health:   OK", out)

    def test_start_refuses_a_taken_port(self):
        code, out, calls = self.run_script("start", FAKE_PORTS_IN_USE="[::]:8000")
        self.assertEqual(code, 1, out)
        self.assertIn("Port 8000 is already in use", out)
        self.assertIn("./alertbot config", out)
        self.assertFalse(self.has(calls, "up -d"))

    def test_a_port_taken_while_starting_is_explained(self):
        code, out, _ = self.run_script("start", FAKE_UP_FAIL=1, FAKE_PORTS_IN_USE_AFTER="")
        self.assertEqual(code, 1, out)
        self.assertIn("couldn't be started", out)

    def test_config_restarts_a_running_bot_with_the_new_settings(self):
        code, out, calls = self.run_script("config", stdin="\n9000\n", FAKE_RUNNING=1)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.env()["ALERTBOT_PORT"], "9000")
        self.assertEqual(self.env()["ALERTBOT_IMAGE"], OLD, "other lines are kept")
        self.assertTrue(self.has(calls, "up -d"))
        code, out, calls = self.run_script("config", stdin="\n\n")
        self.assertIn("./alertbot start", out, "a stopped bot isn't started")
        self.assertFalse(self.has(calls, "up -d"))

    def test_a_backup_and_its_checked_copy(self):
        code, out, calls = self.run_script("backup")
        self.assertEqual(code, 0, out)
        name = "db-manual-20261024-000000-000001.sqlite3"
        self.assertEqual((self.dir / "backups" / name).read_bytes(), f"backup-bytes-{name}".encode())
        self.assertFalse(list((self.dir / "backups").glob(".*.part")))
        self.assertTrue(self.has(calls, "run --rm --no-deps -T web backup --label manual"))
        self.assertFalse(self.has(calls, "-v " + str(self.dir)), "no folder of this computer is mounted")

    def test_a_failed_backup_says_so(self):
        code, out, _ = self.run_script("backup", FAIL_ON="--label manual")
        self.assertEqual(code, 1, out)
        self.assertIn("The backup failed", out)


class RestoreTests(LinuxScriptTestCase):
    def setUp(self):
        super().setUp()
        self.installed(ALERTBOT_PORT="8080")
        self.backups = "db-before-update-20261020-000000-000001.sqlite3 db-manual-20261023-000000-000001.sqlite3"

    def test_a_restore(self):
        code, out, calls = self.run_script("restore", ALERTBOT_RESTORE_CHOICE=1, ALERTBOT_ASSUME_YES=1,
                                           FAKE_VOLUME_BACKUPS=self.backups)
        self.assertEqual(code, 0, out)
        self.assertTrue((self.dir / "backups" / "db-manual-20261023-000000-000001.sqlite3").exists(), "copied out")
        self.assertIn("Done. The bot is running with the data from", out)
        order = [c for c in calls if any(s in c for s in ("restore --report", " stop", "--label before-restore",
                                                          "restore --expect", "up -d"))]
        self.assertEqual(len(order), 5, order)
        self.assertIn("restore --report /data/restore-staging/", order[0])
        self.assertIn("restore --expect none /data/restore-staging/", order[3])
        self.assertTrue(self.has(calls, "--entrypoint rm"), "the staged copy is removed")
        self.assertEqual(self.env()["ALERTBOT_PORT"], "8080", "settings are kept")

    def test_a_backup_from_another_computer(self):
        (self.dir / "backups").mkdir()
        (self.dir / "backups" / "db-manual-20260101-000000-000001.sqlite3").write_bytes(b"from windows")
        code, out, _ = self.run_script("restore", ALERTBOT_RESTORE_CHOICE=1, ALERTBOT_ASSUME_YES=1)
        self.assertEqual(code, 0, out)
        self.assertEqual(Path(str(self.log) + ".staged").read_bytes(), b"from windows", "sent into Docker as is")

    def test_losing_data_needs_restore_typed(self):
        code, out, calls = self.run_script("restore", ALERTBOT_RESTORE_CHOICE=1, FAKE_REPORT=10,
                                           FAKE_VOLUME_BACKUPS=self.backups, stdin="yes\n")
        self.assertEqual(code, 0, out)
        self.assertIn("Cancelled. Nothing was changed.", out)
        self.assertFalse(self.has(calls, "restore --expect"))
        code, out, calls = self.run_script("restore", ALERTBOT_RESTORE_CHOICE=1, FAKE_REPORT=10,
                                           FAKE_VOLUME_BACKUPS=self.backups, ALERTBOT_RESTORE_CONFIRM="RESTORE")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.has(calls, "restore --expect lost-1"))

    def test_a_backup_of_a_newer_version_is_refused(self):
        code, out, calls = self.run_script("restore", ALERTBOT_RESTORE_CHOICE=1, ALERTBOT_ASSUME_YES=1, FAKE_REPORT=3,
                                           FAKE_VOLUME_BACKUPS=self.backups)
        self.assertEqual(code, 1, out)
        self.assertIn("made by a newer version", out)
        self.assertFalse(self.has(calls, " stop"))

    def test_data_used_elsewhere_restarts_the_bot_unchanged(self):
        code, out, calls = self.run_script("restore", ALERTBOT_RESTORE_CHOICE=1, ALERTBOT_ASSUME_YES=1,
                                           FAKE_VOLUME_BACKUPS=self.backups, FAKE_DATA_USERS="other-bot-1")
        self.assertEqual(code, 1, out)
        self.assertIn("other-bot-1", out)
        self.assertFalse(self.has(calls, "restore --expect"))
        self.assertTrue(self.has(calls, "up -d"))


class UpdateTests(LinuxScriptTestCase):
    def setUp(self):
        super().setUp()
        self.installed(ALERTBOT_BIND="192.168.1.10", ALERTBOT_PORT="8080",
                       ALERTBOT_ALLOWED_HOSTS="localhost,127.0.0.1,[::1],192.168.1.10",
                       ALERTBOT_CSRF_TRUSTED_ORIGINS="http://192.168.1.10:8080")
        self.old_script = (self.dir / "alertbot").read_bytes()
        self.write_bundle(lambda d: (d / "README.txt").write_bytes(b"new version\n"))

    def update(self, **fake):
        fake.setdefault("FAKE_BUNDLE_DIR", self.bundle)
        fake.setdefault("FAKE_RUNNING", 1)
        fake.setdefault("ALERTBOT_ASSUME_YES", 1)
        return self.run_script("update", **fake)

    def state(self):
        path = self.dir / "docker" / "update" / "state.txt"
        return dict(line.split("=", 1) for line in path.read_text().splitlines()) if path.exists() else {}

    def test_up_to_date_changes_nothing(self):
        code, out, calls = self.update(FAKE_PLAN="up_to_date")
        self.assertEqual(code, 0, out)
        self.assertIn("You have the newest version", out)
        for step in (" stop", "up -d", "--label", "pull", "create"):
            self.assertFalse(self.has(calls, step), step)
        self.assertIn("REVISION=2", (self.dir / "docker" / "update" / "seen.txt").read_text())
        self.assertTrue(self.has(calls, "update-plan --channel prerelease"))

    def test_network_failure_and_bad_metadata_change_nothing(self):
        for fake in ({"FAKE_PLAN": "blocked"}, {"FAKE_PLAN": ""}):
            code, out, calls = self.update(**fake)
            self.assertEqual(code, 1, out)
            self.assertIn("Nothing was changed", out)
            self.assertFalse(self.has(calls, " stop"))

    def test_a_successful_update(self):
        code, out, calls = self.update(FAKE_PLAN="update")
        self.assertEqual(code, 0, out)
        self.assertIn("The update is complete: 1.0.0-rc.4 is running", out)
        self.assertEqual((self.dir / "README.txt").read_bytes(), b"new version\n")
        env = self.env()
        self.assertEqual(env["ALERTBOT_IMAGE"], NEW)
        self.assertEqual((env["ALERTBOT_BIND"], env["ALERTBOT_PORT"]), ("192.168.1.10", "8080"), "settings kept")
        self.assertEqual(env["ALERTBOT_CSRF_TRUSTED_ORIGINS"], "http://192.168.1.10:8080")
        self.assertEqual((self.dir / "docker" / "release.env").read_text(), f"ALERTBOT_IMAGE={NEW}\n")
        self.assertEqual((self.dir / "docker" / "update" / "previous" / "alertbot").read_bytes(), self.old_script)
        self.assertEqual(self.state()["STATE"], "DONE")
        steps = ["update-plan", "describe", "-update-files", "update-preflight", " stop", "--label before-update",
                 " trial", " fingerprint", "up -d"]
        positions = [next(i for i, c in enumerate(calls) if s in c) for s in steps]
        self.assertEqual(positions, sorted(positions), "in this order")
        self.assertTrue(self.has(calls, "--network none --read-only --tmpfs /tmp --cap-drop ALL"))

    def test_checks_before_stopping_abort_without_changes(self):
        cases = {"FAKE_TARGET_VERSION": ("9.9.9", "isn't version 1.0.0-rc.4"),
                 "FAKE_CP_FAIL": (1, "couldn't be copied out"),
                 "FAKE_PREFLIGHT": (1, "can't take over your data"),
                 "FAKE_IMAGE_MISSING": ("1.0.0-rc.4", "couldn't be downloaded")}
        for key, (value, message) in cases.items():
            # A missing image is downloaded; here the download fails too.
            code, out, calls = self.update(FAKE_PLAN="update", FAKE_PULL_FAIL=1, **{key: value})
            self.assertEqual(code, 1, key)
            self.assertIn(message, out, key)
            self.assertIn("stopped before anything changed", out, key)
            self.assertFalse(self.has(calls, " stop"), key)
            self.assertEqual(self.env()["ALERTBOT_IMAGE"], OLD, key)
            self.assertEqual(self.state()["STATE"], "ABORTED", key)

    def test_a_file_that_doesnt_match_its_hash(self):
        info = self.write_bundle()
        (self.bundle / "linux" / "alertbot").write_bytes(b"#!/bin/sh\necho tampered\n")
        code, out, calls = self.update(FAKE_PLAN="update")
        self.assertEqual(code, 1, out)
        self.assertIn("didn't check out (alertbot)", out)
        self.assertEqual((self.dir / "alertbot").read_bytes(), self.old_script)
        self.assertTrue(info)

    def test_data_in_use_or_a_failed_backup_restart_the_bot_as_it_was(self):
        for fake, message in (({"FAKE_DATA_USERS": "elsewhere-1"}, "elsewhere-1"),
                              ({"FAIL_ON": "--label before-update"}, "The backup failed")):
            code, out, calls = self.update(FAKE_PLAN="update", **fake)
            self.assertEqual(code, 1, out)
            self.assertIn(message, out)
            self.assertIn("started again as it was", out)
            self.assertTrue(self.has(calls, "up -d"))
            self.assertEqual(self.env()["ALERTBOT_IMAGE"], OLD)

    def assertRolledBack(self, code, out, calls):
        self.assertEqual(code, 1, out)
        self.assertIn("the previous version is back", out)
        self.assertEqual((self.dir / "alertbot").read_bytes(), self.old_script)
        self.assertNotEqual((self.dir / "README.txt").read_bytes(), b"new version\n")
        env = self.env()
        self.assertEqual(env["ALERTBOT_IMAGE"], OLD)
        self.assertEqual(env["ALERTBOT_PORT"], "8080")
        self.assertTrue(self.has(calls, f"{OLD} restore /data/backups/db-before-update-"), "with the old version")
        self.assertEqual(self.state()["STATE"], "ROLLED_BACK")

    def test_a_failed_trial_goes_back(self):
        code, out, calls = self.update(FAKE_PLAN="update", FAKE_TRIAL=1)
        self.assertIn("failed in the trial run", out)
        self.assertRolledBack(code, out, calls)

    def test_a_version_that_doesnt_start_goes_back_if_nothing_was_written(self):
        code, out, calls = self.update(FAKE_PLAN="update", FAKE_BROKEN_IMAGE="1.0.0-rc.4")
        self.assertIn("1.0.0-rc.4 didn't start properly", out)
        self.assertTrue((self.dir / "logs" / "problem-report.txt").exists())
        self.assertRolledBack(code, out, calls)

    def test_data_written_by_the_new_version_is_left_to_the_user(self):
        code, out, calls = self.update(FAKE_PLAN="update", FAKE_BROKEN_IMAGE="1.0.0-rc.4", FAKE_FP_LATER="b" * 20)
        self.assertEqual(code, 1, out)
        self.assertIn("STOP:", out)
        self.assertFalse(self.has(calls, " restore /data/backups/"))
        self.assertEqual(self.state()["STATE"], "NEEDS_USER")
        code, out, _ = self.update(FAKE_PLAN="up_to_date", ALERTBOT_UPDATE_RESOLVE="BACK")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.state()["STATE"], "ROLLED_BACK")

    def seed(self, state):
        upd = self.dir / "docker" / "update"
        (upd / "previous" / "docker").mkdir(parents=True, exist_ok=True)
        files = []
        for name in ("alertbot", "README.txt", "docker/compose.env"):
            shutil.copyfile(self.dir / name, upd / "previous" / name)
            files.append(name)
        (upd / "previous" / ".files").write_text("\n".join(files) + "\n")
        (self.dir / "README.txt").write_bytes(b"half-installed")
        text = (self.dir / "docker" / "compose.env").read_text().replace(OLD, NEW)
        (self.dir / "docker" / "compose.env").write_text(text)
        (upd / "state.txt").write_text(
            f"STATE={state}\nOLD_IMAGE={OLD}\nNEW_IMAGE={NEW}\nTARGET_VERSION=1.0.0-rc.4\n"
            f"BACKUP_FILE=db-before-update-20261024-000000-000001.sqlite3\nFP_TRIAL=aaaaaaaaaaaaaaaaaaaa\nWAS_RUNNING=1\n")

    def test_an_update_interrupted_after_the_swap_is_undone(self):
        self.seed("SWAPPED")
        code, out, calls = self.update(FAKE_PLAN="up_to_date")
        self.assertIn("stopped part-way (at SWAPPED)", out)
        self.assertRolledBack(code, out, calls)
        self.assertFalse(self.has(calls, "update-plan"), "it stops after undoing")

    def test_a_rollback_interrupted_after_the_restore_doesnt_restore_again(self):
        self.seed("RESTORED")
        (self.dir / "docker" / "compose.env").write_text(
            (self.dir / "docker" / "compose.env").read_text().replace(NEW, OLD))
        code, out, calls = self.update(FAKE_PLAN="up_to_date")
        self.assertIn("Your data had already been put back", out)
        self.assertFalse(self.has(calls, " restore /data/backups/"))
        self.assertTrue(self.has(calls, "up -d"))
        self.assertEqual(self.state()["STATE"], "ROLLED_BACK")

    def test_an_update_interrupted_while_starting_that_had_worked(self):
        self.seed("STARTING")
        code, out, calls = self.update(FAKE_PLAN="up_to_date")
        self.assertEqual(code, 0, out)
        self.assertIn("It had finished: 1.0.0-rc.4 is running", out)
        self.assertFalse(self.has(calls, " restore /data/backups/"))

    def test_other_commands_wait_for_an_unfinished_update(self):
        self.seed("TRIAL_OK")
        for command in ("start", "setup", "restore", "config"):
            code, out, calls = self.run_script(command, ALERTBOT_RESTORE_CHOICE=1)
            self.assertEqual(code, 1, command)
            self.assertIn("Run ./alertbot update first", out, command)
            self.assertFalse(self.has(calls, "up -d"), command)

    def test_one_update_at_a_time(self):
        code, out, calls = self.update(FAKE_PLAN="update", FAKE_LOCK_HELD=1)
        self.assertEqual(code, 1, out)
        self.assertIn("Another update is running", out)
        self.assertFalse(self.has(calls, " stop"))
        code, out, _ = self.update(FAKE_PLAN="update", FAKE_LOCK_HELD_ONCE=1, ALERTBOT_UPDATE_TAKE_LOCK="YES")
        self.assertEqual(code, 0, out)

    def test_a_running_bot_of_another_version_isnt_updated(self):
        code, out, calls = self.update(FAKE_PLAN="update", FAKE_OTHER_VERSION=1)
        self.assertEqual(code, 1, out)
        self.assertIn("isn't this folder's version", out)
        self.assertFalse(self.has(calls, " stop"))

    def test_the_new_script_runs_only_next_time(self):
        # The running ./alertbot is replaced by a rename, so this run keeps reading its own copy.
        self.write_bundle(lambda d: (d / "alertbot").write_bytes(b"#!/usr/bin/env bash\necho NEW SCRIPT RAN\nexit 0\n"))
        code, out, _ = self.update(FAKE_PLAN="update")
        self.assertEqual(code, 0, out)
        self.assertNotIn("NEW SCRIPT RAN", out)
        self.assertIn("The update is complete", out)
        self.assertEqual(hashlib.sha256((self.dir / "alertbot").read_bytes()).hexdigest(),
                         hashlib.sha256(b"#!/usr/bin/env bash\necho NEW SCRIPT RAN\nexit 0\n").hexdigest())
