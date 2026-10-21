"""Step 8: Docker deployment files and the Windows scripts.

- Static checks of the Dockerfile, Compose file, .dockerignore and entrypoint.
- The real setup.bat / start.bat / update.bat / stop.bat / backup.bat run under cmd.exe
  in a temporary copy of the project, with a fake `docker` and `curl`
  (config/testing/fake_docker.cmd). No real Docker, no network, no real data.
  Windows only; skipped elsewhere.

Tests that use real Docker containers are in config/tests_docker.py (opt-in).
"""
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)
TESTING = ROOT / "config" / "testing"
SCRIPTS = ["setup.bat", "start.bat", "update.bat", "stop.bat", "backup.bat", "restore.bat"]
ALL_BATCH = SCRIPTS + ["docker/scripts/common.bat", "dev/setup-without-docker.bat", "dev/start-without-docker.bat"]


def compose_text():
    return (ROOT / "docker-compose.yml").read_text(encoding="utf-8")


class DockerFilesTests(SimpleTestCase):
    def test_panel_is_published_on_this_computer_only(self):
        ports = re.findall(r'^\s*-\s*"([^"]+):8000"\s*$', compose_text(), flags=re.M)
        self.assertEqual(ports, ["127.0.0.1:${ALERTBOT_PORT:-8000}"])
        self.assertNotIn("0.0.0.0:", compose_text())

    def test_services_restart_on_their_own_and_debug_is_off(self):
        text = compose_text()
        self.assertIn("restart: unless-stopped", text)
        self.assertIn('DEBUG: "False"', text)
        self.assertIn('ALLOWED_HOSTS: "localhost,127.0.0.1,[::1]"', text)
        self.assertNotIn("SETTINGS_ENV_MIRROR_PATH", text, "removed in step 10: nothing writes .env")
        for service in ("web:", "checker:"):
            self.assertIn(f"  {service}\n    <<: *bot", text)
        self.assertEqual(text.count("healthcheck:"), 2)

    def test_all_data_is_in_one_named_volume(self):
        text = compose_text()
        self.assertIn("- bot-data:/data", text)
        for path in ("DATABASE_PATH: /data/db.sqlite3", "BACKUP_DIR: /data/backups", "SECRET_KEY_FILE: /data/secret_key"):
            self.assertIn(path, text)
        self.assertRegex(text, r"\nvolumes:\n  bot-data:\n")
        self.assertIn("name: alertbot", text, "a fixed project name keeps the volume name stable")

    def test_compose_file_is_valid(self):
        if shutil.which("docker") is None:
            self.skipTest("the docker command isn't installed")
        for files in (["-f", "docker-compose.yml"], ["-f", "docker-compose.yml", "-f", "docker-compose.build.yml"]):
            res = subprocess.run(["docker", "compose", "--env-file", "docker/compose.env", *files, "config", "-q"],
                                 cwd=ROOT, capture_output=True, text=True, timeout=60)
            self.assertEqual(res.returncode, 0, res.stderr)

    def test_nothing_private_goes_into_the_image(self):
        ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
        for pattern in (".env", "*.sqlite3", "backups/", "venv/", ".git/", "logs/"):
            self.assertIn(pattern, ignore)
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("test ! -e /app/.env && test ! -e /app/db.sqlite3 && test ! -e /app/backups", dockerfile)
        self.assertIn("USER bot", dockerfile)

    def test_the_control_panel_is_built_in_its_own_stage(self):
        """Step 9: users need no Node.js; it's used only while building the image."""
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        stages = re.findall(r"^FROM\s+(.+)$", dockerfile, flags=re.M)
        self.assertEqual(len(stages), 3)
        self.assertIn("node:", stages[0])
        self.assertIn("AS panel", stages[0])
        self.assertIn("--platform=$BUILDPLATFORM", stages[0], "built once, natively, for every platform")
        self.assertTrue(stages[-1].startswith("python:"), "the image that runs has no Node.js")
        self.assertIn("npm ci --no-audit --no-fund --ignore-scripts", dockerfile, "exact versions from the lockfile")
        self.assertIn("COPY --from=panel /panel/dist /app/frontend/dist", dockerfile)
        self.assertIn("test -f /app/frontend/dist/index.html", dockerfile, "the build fails without the panel")
        ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
        for pattern in ("**/node_modules/", "frontend/dist/"):
            self.assertIn(pattern, ignore, "local builds and installs never go into the image")

    def test_image_uses_the_exact_tested_versions(self):
        lock = (ROOT / "requirements-lock.txt").read_text(encoding="utf-8")
        for line in lock.splitlines():
            if line and not line.startswith("#"):
                self.assertRegex(line, r"^[A-Za-z0-9_.\-]+==[\w.\-]+$")
        self.assertIn("Django==5.2.18", lock)
        self.assertNotIn("django-apscheduler", lock, "unused; removed in step 10")
        self.assertIn("-r requirements-lock.txt", (ROOT / "requirements-docker.txt").read_text(encoding="utf-8"))

    def test_entrypoint_backs_up_and_migrates_before_serving(self):
        entry = (ROOT / "docker" / "entrypoint.sh").read_bytes()
        self.assertNotIn(b"\r\n", entry, "Linux shell scripts need LF line endings")
        text = entry.decode()
        web = text[text.index("  web)"):text.index("  checker)")]
        self.assertLess(web.index("prepare_data"), web.index("exec gunicorn"))
        checker = text[text.index("  checker)"):text.index("  import-old)")]
        self.assertLess(checker.index("wait_for_database"), checker.index("run_scheduler"))
        self.assertIn("eol=lf", (ROOT / ".gitattributes").read_text(encoding="utf-8"))


class BatchFileTests(SimpleTestCase):
    def test_batch_files_are_plain_ascii_with_windows_line_endings(self):
        for name in ALL_BATCH:
            data = (ROOT / name).read_bytes()
            data.decode("ascii")
            self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), f"{name} must use CRLF line endings")

    # Anything that could delete the bot's data. (Until step 10 these patterns held stray control
    # characters instead of \b, so most of them could never match; test_the_guard_catches_* checks them.)
    FORBIDDEN = (r"compose\b.*\bdown\b", r"volume\s+rm\b", r"volume\s+prune\b", r"system\s+prune\b",
                 r"\bprune\b", r"--volumes\b", r"\brmdir\b", r"\brd\s", r"\bdel\s", r"\berase\b")
    # The one allowed prune: after a successful update, this bot's own unused images (never volumes).
    ALLOWED = '%alertbot_docker% image prune -f --filter "label=org.opencontainers.image.title=bluesky discord alert bot" >nul 2>&1'

    def dangerous(self, line):
        line = line.lower().strip()
        if line.startswith("rem") or line.startswith("echo") or line == self.ALLOWED:
            return []
        import re
        return [p for p in self.FORBIDDEN if re.search(p, line)]

    def test_scripts_never_remove_data(self):
        for name in ALL_BATCH:
            for line in (ROOT / name).read_text(encoding="ascii").splitlines():
                self.assertEqual(self.dangerous(line), [], f"{name}: {line}")

    def test_the_guard_catches_every_kind_of_deletion(self):
        for line in ("%COMPOSE% down", "%COMPOSE% down --volumes", "docker volume rm alertbot_bot-data",
                     "docker volume prune -f", "docker system prune -a", "docker image prune -a",
                     "rmdir /s /q backups", "rd /s /q backups", "del backups\\*.sqlite3", "erase db.sqlite3"):
            self.assertNotEqual(self.dangerous(line), [], line)
        for line in ("rem never runs down", "echo  Nothing is deleted.", "%COMPOSE% stop", "%COMPOSE% up -d",
                     self.ALLOWED.upper()):
            self.assertEqual(self.dangerous(line), [], line)

    def test_the_patterns_are_clean(self):
        for pattern in self.FORBIDDEN:
            self.assertTrue(pattern.isprintable(), repr(pattern))

    def test_old_start_script_still_listens_locally(self):
        text = (ROOT / "dev" / "start-without-docker.bat").read_text(encoding="ascii")
        self.assertIn("runserver 127.0.0.1:8000", text)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ScriptHarness(SimpleTestCase):
    """Runs the real scripts with cmd.exe against a fake docker. Each run takes about a second."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / "bot folder"  # a space in the path, like many Windows user folders
        for name in SCRIPTS + ["docker-compose.yml", "docker-compose.build.yml", "Dockerfile"]:
            (self.dir / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, self.dir / name)
        shutil.copytree(ROOT / "docker", self.dir / "docker")
        self.log = Path(tmp.name) / "calls.log"
        self.port = free_port()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("ALERTBOT_", "FAKE_"))}
        self.env.update(
            ALERTBOT_DOCKER=f'call "{TESTING / "fake_docker.cmd"}"',
            ALERTBOT_CURL=f'call "{TESTING / "fake_curl.cmd"}"',
            ALERTBOT_NO_BROWSER="1", ALERTBOT_NO_PAUSE="1", ALERTBOT_ASSUME_YES="1", ALERTBOT_NO_DESKTOP_START="1",
            ALERTBOT_DOCKER_WAIT_SECONDS="0", ALERTBOT_READY_WAIT_SECONDS="0", ALERTBOT_PORT=str(self.port),
            ALERTBOT_DESKTOP_GRACE_SECONDS="0",
            ALERTBOT_DESKTOP_RUNNING="0",  # never look at (or restart) the real Docker Desktop
            FAKE_DOCKER_LOG=str(self.log), FAKE_ENGINE_UP_FILE=str(Path(tmp.name) / "engine-up"),
        )

    def run_script(self, name, **fake):
        env = {**self.env, **{k: str(v) for k, v in fake.items()}}
        res = subprocess.run(["cmd", "/d", "/c", str(self.dir / name)], cwd=self.dir, env=env, stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, encoding="ascii", errors="replace", timeout=120)
        calls = self.log.read_text(encoding="ascii").splitlines() if self.log.exists() else []
        self.log.unlink(missing_ok=True)
        return res.returncode, res.stdout + res.stderr, calls

    def compose_calls(self, calls):
        return [c.split(" -f docker-compose.yml", 1)[1].replace(" -f docker-compose.build.yml", "").strip()
                for c in calls if c.startswith("docker compose -p")]


@unittest.skipUnless(sys.platform == "win32", "the .bat scripts run on Windows")
class WindowsScriptTests(ScriptHarness):

    # -- Docker Desktop problems ----------------------------------------------------------------
    def test_docker_desktop_not_running_is_explained_and_nothing_is_started(self):
        for name in SCRIPTS:
            code, out, calls = self.run_script(name, FAKE_ENGINE="down")
            self.assertEqual(code, 1, name)
            self.assertIn("Docker Desktop isn't running yet", out, name)
            self.assertIn("Engine running", out)
            self.assertEqual(self.compose_calls(calls), [], f"{name} did nothing else")

    def test_a_stuck_docker_desktop_is_restarted_once_and_then_works(self):
        code, out, calls = self.run_script("start.bat", FAKE_ENGINE="down", ALERTBOT_DESKTOP_RUNNING="1",
                                           ALERTBOT_DESKTOP_RESTART=f'"{TESTING / "fake_restart.cmd"}"')
        self.assertEqual(code, 0, out)
        self.assertIn("Docker Desktop is open, but it isn't responding", out)
        self.assertIn("The bot's data isn't affected", out)
        self.assertEqual(calls.count("restart-desktop"), 1)
        self.assertEqual(self.compose_calls(calls)[-1], "up -d")

    def test_a_restart_that_doesnt_help_is_explained_and_not_repeated(self):
        code, out, calls = self.run_script("start.bat", FAKE_ENGINE="down", ALERTBOT_DESKTOP_RUNNING="1",
                                           ALERTBOT_DESKTOP_RESTART=f'"{TESTING / "fake_restart.cmd"}" broken')
        self.assertEqual(code, 1)
        self.assertEqual(calls.count("restart-desktop"), 1)
        self.assertIn("isn't running yet, or it isn't working right now", out)
        self.assertEqual(self.compose_calls(calls), [])

    def test_the_real_docker_desktop_is_never_restarted_by_the_tests(self):
        common = (ROOT / "docker" / "scripts" / "common.bat").read_text(encoding="ascii")
        self.assertIn("if defined CUSTOM_DOCKER if not defined ALERTBOT_DESKTOP_RESTART goto :msg_engine_down", common)
        self.assertEqual(self.env["ALERTBOT_DESKTOP_RUNNING"], "0")

    def test_old_docker_desktop_is_explained(self):
        code, out, _ = self.run_script("start.bat", FAKE_COMPOSE="missing")
        self.assertEqual(code, 1)
        self.assertIn("Docker Desktop is too old", out)

    def test_windows_containers_mode_is_explained(self):
        code, out, _ = self.run_script("setup.bat", FAKE_OSTYPE="windows")
        self.assertEqual(code, 1)
        self.assertIn("Switch to Linux containers", out)

    # -- setup ----------------------------------------------------------------------------------
    def test_setup_builds_imports_the_old_install_starts_and_waits(self):
        (self.dir / "db.sqlite3").write_bytes(b"old database")
        (self.dir / ".env").write_text("BLUESKY_HANDLE=fake.bsky.social\n", encoding="ascii")
        code, out, calls = self.run_script("setup.bat", FAKE_VOLUME="missing")
        self.assertEqual(code, 0, out)
        steps = self.compose_calls(calls)
        self.assertEqual(steps[0], "ps -q web")
        self.assertEqual(steps[1], "build")
        self.assertEqual(steps[2], f'run --rm --no-deps -v "{self.dir}:/import:ro" '
                                   "-e LEGACY_ENV_FILE=/import/.env web import-old")
        self.assertEqual(steps[3], "up -d")
        self.assertTrue(any(c.startswith("curl") and f"127.0.0.1:{self.port}/api/health/" in c for c in calls))
        self.assertIn("Setup is complete", out)
        self.assertEqual((self.dir / "db.sqlite3").read_bytes(), b"old database", "the old files are not changed")

    def test_setup_without_an_old_install_skips_the_import(self):
        code, out, calls = self.run_script("setup.bat", FAKE_VOLUME="missing")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.compose_calls(calls), ["ps -q web", "build", "up -d"])

    def test_setup_run_again_never_imports_over_existing_data(self):
        (self.dir / "db.sqlite3").write_bytes(b"old database")
        code, out, calls = self.run_script("setup.bat")  # the volume exists
        self.assertEqual(code, 0, out)
        self.assertNotIn("import-old", " ".join(calls))

    def test_release_download_pulls_the_published_image_instead_of_building(self):
        env_file = self.dir / "docker" / "compose.env"
        env_file.write_text(env_file.read_text().replace("ALERTBOT_IMAGE=\n", "ALERTBOT_IMAGE=example/alertbot:stable\n"))
        code, out, calls = self.run_script("setup.bat", FAKE_VOLUME="missing")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.compose_calls(calls), ["ps -q web", "pull", "up -d"])
        self.assertFalse(any("docker-compose.build.yml" in c for c in calls))

    def test_a_failed_build_stops_setup_with_a_clear_message(self):
        code, out, calls = self.run_script("setup.bat", FAKE_VOLUME="missing", FAKE_FAIL_ON=" build")
        self.assertEqual(code, 1)
        self.assertIn("Check that this computer is connected", out)
        self.assertIn("safe to run setup.bat again", out)
        self.assertNotIn("up -d", self.compose_calls(calls))

    def test_panel_not_answering_saves_a_problem_report(self):
        code, out, calls = self.run_script("start.bat", FAKE_CURL_EXIT=7)
        self.assertEqual(code, 1)
        self.assertIn("didn't answer in time", out)
        report = self.dir / "logs" / "problem-report.txt"
        self.assertTrue(report.exists())
        self.assertIn("logs --no-color --tail 300", " ".join(calls))

    def test_a_busy_port_is_explained_before_anything_starts(self):
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", self.port))
            busy.listen()
            code, out, calls = self.run_script("start.bat")
        self.assertEqual(code, 1)
        self.assertIn(f"Another program is already using port {self.port}", out)
        self.assertIn("close that window", out)
        self.assertNotIn("up -d", self.compose_calls(calls))

    # -- start ----------------------------------------------------------------------------------
    def test_start_starts_and_opens_when_ready(self):
        code, out, calls = self.run_script("start.bat")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.compose_calls(calls), ["ps -q web", "up -d"])
        self.assertIn(f"The control panel is ready: http://127.0.0.1:{self.port}/", out)

    def test_start_before_setup_runs_the_setup(self):
        code, out, calls = self.run_script("start.bat", FAKE_VOLUME="missing")
        self.assertEqual(code, 0, out)
        self.assertIn("Starting the setup", out)
        self.assertIn("build", self.compose_calls(calls))

    # -- update ---------------------------------------------------------------------------------
    def test_update_backs_up_first_then_rebuilds_and_restarts(self):
        code, out, calls = self.run_script("update.bat")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.compose_calls(calls), [
            f'run --rm --no-deps -v "{self.dir}\\backups:/export" web backup --label before-update --export /export',
            "build --pull",
            "up -d",
        ])
        self.assertTrue((self.dir / "backups").is_dir())
        self.assertIn("The update is complete", out)
        self.assertEqual(calls[-1], 'docker image prune -f --filter "label=org.opencontainers.image.title=Bluesky '
                                    'Discord Alert Bot"', "old images of this bot are removed after a good update")

    def test_update_stops_if_the_backup_fails(self):
        code, out, calls = self.run_script("update.bat", FAKE_FAIL_ON="backup")
        self.assertEqual(code, 1)
        self.assertIn("The backup didn't work, so nothing else was done", out)
        self.assertEqual(len(self.compose_calls(calls)), 1, "nothing after the failed backup")

    def test_failed_download_keeps_the_current_version_running(self):
        code, out, calls = self.run_script("update.bat", FAKE_FAIL_ON=" build --pull")
        self.assertEqual(code, 1)
        self.assertIn("The current version keeps running", out)
        self.assertEqual(self.compose_calls(calls)[-1], "up -d")

    def test_update_reports_a_new_version_that_doesnt_start(self):
        code, out, calls = self.run_script("update.bat", FAKE_CURL_EXIT=7)
        self.assertEqual(code, 1)
        self.assertFalse(any("prune" in c for c in calls), "nothing is tidied after a failed update")
        self.assertIn("A backup from just before the update is in the backups folder", out)

    # -- stop and backup ------------------------------------------------------------------------
    def test_stop_only_stops(self):
        code, out, calls = self.run_script("stop.bat")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.compose_calls(calls), ["stop"])
        self.assertIn("Your accounts, settings and history are kept", out)

    def test_backup_exports_a_copy(self):
        code, out, calls = self.run_script("backup.bat")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.compose_calls(calls), [
            f'run --rm --no-deps -v "{self.dir}\\backups:/export" web backup --label manual --export /export'])

    # -- restore ---------------------------------------------------------------------------------
    def make_backups(self, *names):
        folder = self.dir / "backups"
        folder.mkdir(exist_ok=True)
        for i, name in enumerate(names):
            path = folder / name
            path.write_bytes(b"backup")
            os.utime(path, (1_700_000_000 + i * 60, 1_700_000_000 + i * 60))

    def test_restore_backs_up_first_then_stops_restores_and_starts(self):
        self.make_backups("db-manual-20261020-100000-000001.sqlite3", "db-before-update-20261021-090000-000001.sqlite3")
        code, out, calls = self.run_script("restore.bat", ALERTBOT_RESTORE_CHOICE="1")
        self.assertEqual(code, 0, out)
        self.assertIn("1. db-before-update-20261021-090000-000001.sqlite3", out, "newest first")
        self.assertIn("2. db-manual-20261020-100000-000001.sqlite3", out)
        self.assertEqual(self.compose_calls(calls), [
            f'run --rm --no-deps -v "{self.dir}\\backups:/export" web backups',
            f'run --rm --no-deps -v "{self.dir}\\backups:/export" web backup --label before-restore --export /export',
            "stop",
            f'run --rm --no-deps -v "{self.dir}\\backups:/restore:ro" web restore "/restore/'
            'db-before-update-20261021-090000-000001.sqlite3"',
            "up -d",
        ])
        self.assertIn("Done. The bot is running with the data from db-before-update-", out)

    def test_restore_with_an_unknown_number_changes_nothing(self):
        self.make_backups("db-manual-20261020-100000-000001.sqlite3")
        code, out, calls = self.run_script("restore.bat", ALERTBOT_RESTORE_CHOICE="7")
        self.assertEqual(code, 1)
        self.assertIn("There is no backup number 7. Nothing was changed.", out)
        self.assertEqual([c for c in self.compose_calls(calls) if not c.endswith("web backups")], [])

    def test_restore_without_backups_says_so(self):
        code, out, calls = self.run_script("restore.bat", ALERTBOT_RESTORE_CHOICE="1")
        self.assertEqual(code, 0, out)
        self.assertIn("No backups were found", out)
        self.assertNotIn("stop", self.compose_calls(calls))

    def test_a_backup_that_cant_be_put_back_restarts_the_bot_unchanged(self):
        self.make_backups("db-manual-20261020-100000-000001.sqlite3")
        code, out, calls = self.run_script("restore.bat", ALERTBOT_RESTORE_CHOICE="1", FAKE_FAIL_ON="web restore")
        self.assertEqual(code, 1)
        self.assertIn("couldn't be put back", out)
        self.assertEqual(self.compose_calls(calls)[-1], "up -d", "the bot is started again with its own data")

    def test_restore_stops_if_the_safety_backup_fails(self):
        self.make_backups("db-manual-20261020-100000-000001.sqlite3")
        code, out, calls = self.run_script("restore.bat", ALERTBOT_RESTORE_CHOICE="1", FAKE_FAIL_ON="before-restore")
        self.assertEqual(code, 1)
        self.assertNotIn("stop", self.compose_calls(calls))

    def test_every_compose_call_uses_the_project_settings(self):
        _, _, calls = self.run_script("update.bat")
        for call in calls:
            if call.startswith("docker compose") and " version" not in call:
                self.assertIn("--env-file docker\\compose.env", call, "never the folder's .env with the old secrets")


@unittest.skipUnless(sys.platform == "win32", "the .bat scripts run on Windows")
class ReleaseBundleTests(ScriptHarness):
    """The download for non-technical users runs the published image; nothing is built."""

    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(ROOT / "docker"))
        self.addCleanup(sys.path.remove, str(ROOT / "docker"))
        from make_release_bundle import make

        bundle = make("ghcr.io/example/bluesky-discord-alert-bot:stable", self.dir.parent / "dist")
        self.assertEqual(bundle.name, "BlueskyDiscordAlertBot-stable.zip")
        import zipfile

        with zipfile.ZipFile(bundle) as zf:
            self.names = sorted(zf.namelist())
            shutil.rmtree(self.dir)
            zf.extractall(self.dir)

    def test_bundle_has_only_what_users_need(self):
        self.assertEqual(self.names, sorted(["setup.bat", "start.bat", "update.bat", "stop.bat", "backup.bat", "restore.bat",
                                             "docker-compose.yml", "docker/compose.env", "docker/scripts/common.bat",
                                             "README.txt"]))
        env = (self.dir / "docker" / "compose.env").read_text()
        self.assertIn("ALERTBOT_IMAGE=ghcr.io/example/bluesky-discord-alert-bot:stable\n", env)

    def test_setup_and_update_from_the_bundle_download_the_image(self):
        code, out, calls = self.run_script("setup.bat", FAKE_VOLUME="missing")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.compose_calls(calls), ["ps -q web", "pull", "up -d"])
        code, out, calls = self.run_script("update.bat")
        self.assertEqual(code, 0, out)
        steps = self.compose_calls(calls)
        self.assertTrue(steps[0].startswith("run --rm --no-deps"))
        self.assertEqual(steps[1:], ["pull", "up -d"])
        self.assertFalse(any("build" in c for c in calls))

    def test_bad_image_names_are_refused(self):
        from make_release_bundle import make

        with self.assertRaises(SystemExit):
            make("not a valid image; rm -rf /", self.dir.parent / "dist2")
