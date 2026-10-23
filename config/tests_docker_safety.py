"""Data safety across real containers: the data lock, and stopping the checker during a send.

Opt-in, because it builds an image and starts containers (about 3-5 minutes):

    set RUN_DOCKER_TESTS=1
    venv\\Scripts\\python manage.py test config.tests_docker_safety

What it shows, with the image built from this folder:
- the web service and the checker hold the data lock in their own containers: a restore in
  another container is refused while they run, and works once they are stopped;
- a migration in one container waits for a checker in another, and changes nothing if the
  checker stays; a container killed with SIGKILL leaves no stale lock behind;
- `docker stop` during a Discord send (fake): the send finishes and is saved, nothing is left
  in "sending", nothing new starts; and the limit of what can be promised: after `docker kill`
  during a send, that notification is sent again (a duplicate, if Discord had stored it).

Isolation: its own image tag, volumes and container names, all starting with "alertbot-test-"
(the image: bluesky-alert-bot:safety-test). Nothing contacts Bluesky or Discord: the Discord
sends are fake and the containers have no network. Cleanup removes only what it created.
"""
import os
import shutil
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)
IMAGE = "bluesky-alert-bot:safety-test"
ENV = {"DEBUG": "False", "ALLOWED_HOSTS": "localhost,127.0.0.1", "DATABASE_PATH": "/data/db.sqlite3",
       "BACKUP_DIR": "/data/backups", "SECRET_KEY_FILE": "/data/secret_key", "CACHE_DIR": "/data/cache",
       "SCHEDULER_IN_WEB": "0", "TZ": "UTC"}
HOLDER = ("import sys, time; sys.path.insert(0, '/app'); from botconfig.datalock import DataLock; "
          "l = DataLock(exclusive=False, db_path='/data/db.sqlite3'); assert l.acquire(timeout=30); "
          "print('held', flush=True); time.sleep(600)")


def docker_available():
    if os.environ.get("RUN_DOCKER_TESTS") != "1" or shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, timeout=60).returncode == 0


@unittest.skipUnless(docker_available(), "set RUN_DOCKER_TESTS=1 with Docker running")
class CrossContainerSafetyTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        subprocess.run(["docker", "build", "-q", "--build-arg", "ALERTBOT_VERSION=safety-test", "-t", IMAGE, "."],
                       cwd=ROOT, check=True, capture_output=True, timeout=1800)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["docker", "image", "rm", IMAGE], capture_output=True, timeout=300)
        super().tearDownClass()

    def setUp(self):
        self.name = f"alertbot-test-{uuid.uuid4().hex[:8]}"
        self.volume = f"{self.name}_bot-data"
        subprocess.run(["docker", "volume", "create", self.volume], check=True, capture_output=True, timeout=60)
        self.containers = []
        self.addCleanup(self.cleanup)
        self.run_in("python", "manage.py", "prepare_data")

    def cleanup(self):
        assert self.name.startswith("alertbot-test-")
        for c in self.containers:
            subprocess.run(["docker", "rm", "-f", c], capture_output=True, timeout=120)
        subprocess.run(["docker", "volume", "rm", "-f", self.volume], capture_output=True, timeout=120)

    def docker_args(self, env=None):
        args = ["--network", "none", "-v", f"{self.volume}:/data"]
        for k, v in {**ENV, **(env or {})}.items():
            args += ["-e", f"{k}={v}"]
        return args

    def run_in(self, *command, ok=True, env=None, timeout=600):
        """A one-off container on the test data (like the scripts' `compose run --rm`)."""
        res = subprocess.run(["docker", "run", "--rm", *self.docker_args(env), IMAGE, *command],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if ok:
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return res

    def start(self, suffix, *command, env=None, entrypoint=None):
        """A long-running container on the test data (like the bot's services, with Docker's init)."""
        name = f"{self.name}-{suffix}"
        self.containers.append(name)
        extra = ["--entrypoint", entrypoint] if entrypoint else []
        subprocess.run(["docker", "run", "-d", "--init", "--name", name, *self.docker_args(env), *extra, IMAGE,
                        *command], check=True, capture_output=True, timeout=120)
        return name

    def logs(self, name):
        res = subprocess.run(["docker", "logs", name], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=60)
        return res.stdout + res.stderr

    def wait_for(self, check, seconds=120, what="condition"):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if check():
                return
            time.sleep(0.5)
        self.fail(f"timed out waiting for {what}")

    def sql(self, query):
        res = self.run_in("python", "-c", "import sqlite3; c = sqlite3.connect('/data/db.sqlite3'); "
                          f"print('\\n'.join('|'.join(map(str, r)) for r in c.execute({query!r})))")
        return res.stdout.strip().splitlines()

    def pending_migrations(self):
        return "[ ]" in self.run_in("python", "manage.py", "showmigrations", "bluesky_monitor").stdout

    # -- the data lock --------------------------------------------------------------------------------
    def test_a_restore_is_refused_while_the_web_service_runs_in_its_own_container(self):
        self.run_in("backup", "--label", "manual")
        backup = self.run_in("sh", "-c", "ls /data/backups/db-manual-*").stdout.strip()
        web = self.start("web", "web")
        self.wait_for(lambda: "Booting worker" in self.logs(web), what="the web service")
        res = self.run_in("restore", backup, ok=False)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("The bot is still running", res.stdout + res.stderr)
        subprocess.run(["docker", "stop", web], check=True, capture_output=True, timeout=120)
        self.assertIn("Restored", self.run_in("restore", backup).stdout)
        leftovers = self.run_in("sh", "-c", "ls -a /data").stdout.split()
        self.assertEqual([f for f in leftovers if "restore" in f or f.endswith(("-wal", "-shm"))], [],
                         "no restore copy and no journal file is left in the data")

    def test_a_migration_waits_for_a_checker_in_another_container(self):
        self.run_in("python", "manage.py", "migrate", "bluesky_monitor", "0005")  # an update is waiting
        holder = self.start("checker", "-c", HOLDER, entrypoint="python")
        self.wait_for(lambda: "held" in self.logs(holder), what="the lock holder")
        res = self.run_in("python", "manage.py", "prepare_data", ok=False, env={"DATA_LOCK_WAIT_SECONDS": "3"})
        self.assertEqual(res.returncode, 75, res.stdout + res.stderr)
        self.assertTrue(self.pending_migrations(), "nothing was migrated while the checker ran")
        subprocess.run(["docker", "kill", holder], check=True, capture_output=True, timeout=120)  # SIGKILL
        began = time.monotonic()
        self.run_in("python", "manage.py", "prepare_data", env={"DATA_LOCK_WAIT_SECONDS": "30"})
        self.assertLess(time.monotonic() - began, 25, "a killed container leaves no stale lock")
        self.assertFalse(self.pending_migrations())

    def test_the_checker_doesnt_start_while_a_restore_or_migration_holds_the_data(self):
        holder = self.start("migration", "-c", HOLDER.replace("exclusive=False", "exclusive=True"),
                            entrypoint="python")
        self.wait_for(lambda: "held" in self.logs(holder), what="the lock holder")
        res = self.run_in("checker", ok=False, env={"DATA_LOCK_WAIT_SECONDS": "3"}, timeout=120)
        self.assertEqual(res.returncode, 75, res.stdout + res.stderr)

    # -- stopping the checker during a send --------------------------------------------------------
    def checker_with_slow_sends(self, seconds, **env):
        self.run_in("sh", "-c", "rm -f /data/db.sqlite3* && python /app/bluesky_monitor/testing/checker_proc.py "
                    "/data/db.sqlite3 setup 2 3")
        return self.start("checker", "/app/bluesky_monitor/testing/checker_proc.py", "/data/db.sqlite3", "run",
                          "/data/discord.log", str(seconds), entrypoint="python",
                          env={"SECRET_KEY": "test-only", "SCHEDULER_WATCHDOG_SECONDS": "0", "BOT_LOG_LEVEL": "INFO",
                               "BLUESKY_HANDLE": "bot.bsky.social", "BLUESKY_PASSWORD": "not-used", **env})

    def fake_discord(self):
        res = self.run_in("sh", "-c", "cat /data/discord.log 2>/dev/null || true")
        return res.stdout

    def statuses(self):
        return sorted(self.sql("SELECT status FROM bluesky_monitor_postdelivery"))

    def test_docker_stop_lets_the_checker_finish_its_send(self):
        checker = self.checker_with_slow_sends(3)
        self.wait_for(lambda: "start a0 post 0" in self.fake_discord(), what="the first send")
        began = time.monotonic()
        subprocess.run(["docker", "stop", "-t", "30", checker], check=True, capture_output=True, timeout=120)
        took = time.monotonic() - began
        code = subprocess.run(["docker", "inspect", "-f", "{{.State.ExitCode}}", checker], capture_output=True,
                              text=True, timeout=60).stdout.strip()
        self.assertEqual(code, "0", self.logs(checker))
        self.assertLess(took, 20)
        log = self.fake_discord()
        self.assertIn("done a0 post 0", log)
        self.assertNotIn("start a0 post 1", log, "no new send after the stop")
        self.assertEqual(self.statuses(), ["pending"] * 5 + ["sent"])
        self.assertIn("The checker stopped cleanly", self.logs(checker))

    def test_what_cant_be_promised_a_killed_send_is_sent_again(self):
        checker = self.checker_with_slow_sends(3, SEND_STALE_SECONDS="1", SCHEDULER_LEASE_SECONDS="1",
                                               ACCOUNT_LOCK_SECONDS="1")
        self.wait_for(lambda: "start a0 post 0" in self.fake_discord(), what="the first send")
        subprocess.run(["docker", "kill", checker], check=True, capture_output=True, timeout=120)  # no clean stop
        self.assertIn("sending", self.statuses(), "the killed send's outcome is unknown")
        again = self.start("checker-again", "/app/bluesky_monitor/testing/checker_proc.py", "/data/db.sqlite3",
                           "run", "/data/discord.log", "0", entrypoint="python",
                           env={"SECRET_KEY": "test-only", "SCHEDULER_WATCHDOG_SECONDS": "0",
                                "SEND_STALE_SECONDS": "1", "SCHEDULER_LEASE_SECONDS": "1", "ACCOUNT_LOCK_SECONDS": "1",
                                "BLUESKY_HANDLE": "bot.bsky.social",
                                "BLUESKY_PASSWORD": "not-used"})
        try:
            self.wait_for(lambda: self.fake_discord().count("start a0 post 0") == 2, seconds=60, what="the resend")
        except AssertionError:
            raise AssertionError(self.logs(again)[-3000:] + "\n" + self.fake_discord() + str(self.statuses()))
        subprocess.run(["docker", "stop", again], capture_output=True, timeout=120)
        self.assertNotIn("sending", self.statuses())
