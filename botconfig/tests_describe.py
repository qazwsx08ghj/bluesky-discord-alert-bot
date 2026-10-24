"""manage.py describe_release (the image's `describe`) and how the image carries its deployment files.

The command runs in separate processes, like in the container, against deployment files exported
the way the image build does it. The real image: config/tests_docker_release.py.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from botconfig import compat

ROOT = Path(settings.BASE_DIR)
sys.path.insert(0, str(ROOT / "docker"))
import make_release_bundle as mrb  # noqa: E402


class DescribeTests(SimpleTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        mrb.export(self.tmp / "bundle" / "windows")
        mrb.export(self.tmp / "bundle" / "linux", platform="linux")
        self.db = self.tmp / "data" / "db.sqlite3"

    def describe(self, version="1.2.0", bundle_dir=None):
        env = {k: v for k, v in os.environ.items() if k != "RUN_MAIN"}
        env.update(ALERTBOT_VERSION=version, DATABASE_PATH=str(self.db), SECRET_KEY="", SECRET_KEY_FILE="",
                   BOT_LOG_LEVEL="CRITICAL", PYTHONIOENCODING="utf-8")
        res = subprocess.run([sys.executable, str(ROOT / "manage.py"), "describe_release", "--bundle-dir",
                              str(bundle_dir or self.tmp / "bundle")], cwd=ROOT, env=env, capture_output=True,
                             text=True, encoding="utf-8", timeout=120)
        return res.returncode, json.loads(res.stdout), res.stderr

    def test_a_complete_image(self):
        code, out, err = self.describe()
        self.assertEqual(code, 0, err)
        self.assertEqual(out["schema"], "alertbot-describe/1")
        self.assertEqual((out["version"], out["version_is_release"]), ("1.2.0", True))
        self.assertEqual(out["problems"], [])
        self.assertEqual(out["bundles"]["windows"]["files"], mrb.index(mrb.bundle_files())["files"])
        self.assertEqual(out["bundles"]["linux"]["files"], mrb.index(mrb.bundle_files(platform="linux"))["files"])
        self.assertRegex(out["platform"], r"^[a-z]+/[a-z0-9]+$")

    def test_only_this_projects_newest_migrations(self):
        _, out, _ = self.describe()
        self.assertEqual(set(out["data_schema"]), {"activity", "bluesky_monitor", "botconfig", "discord_alerts"})
        for app, name in out["data_schema"].items():
            self.assertIn((app, name), compat.known_migrations())
            newer = [n for a, n in compat.known_migrations() if a == app and n > name and re.match(r"\d{4}_", n)]
            self.assertEqual(newer, [], f"{name} is {app}'s newest migration")

    def test_a_development_build_says_so(self):
        code, out, _ = self.describe(version="dev")
        self.assertEqual((code, out["version_is_release"]), (0, False))

    def test_changed_or_missing_deployment_files_are_a_problem(self):
        with open(self.tmp / "bundle" / "windows" / "update.bat", "ab") as f:
            f.write(b"rem changed\r\n")
        code, out, _ = self.describe()
        self.assertEqual(code, 1)
        self.assertIn("update.bat doesn't match its hash", " ".join(out["problems"]))
        self.assertEqual(list(out["bundles"]), ["linux"], "only the intact set is listed")
        (self.tmp / "bundle" / "linux" / "alertbot").unlink()
        code, out, _ = self.describe()
        self.assertIn("linux deployment files", " ".join(out["problems"]))
        self.assertEqual(out["bundles"], {})
        code, out, _ = self.describe(bundle_dir=self.tmp / "nowhere")
        self.assertEqual(code, 1)
        self.assertIn("have no bundle.json", " ".join(out["problems"]))

    def test_it_touches_no_data(self):
        self.describe()
        self.assertFalse(self.db.parent.exists(), "no database, folder or lock file is created")


class ImageLayoutTests(SimpleTestCase):
    """Static checks of how the image carries the deployment files (the CI Docker job builds it)."""

    def test_the_build_exports_the_deployment_files_and_checks_them(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("python docker/make_release_bundle.py --export /app/bundle/windows", dockerfile)
        self.assertIn("test ! -e /app/bundle/windows/docker/compose.env", dockerfile)
        self.assertIn("python docker/make_release_bundle.py --platform linux --export /app/bundle/linux", dockerfile)
        self.assertIn("test -x /app/bundle/linux/alertbot", dockerfile)
        export_at = dockerfile.index("--export /app/bundle/windows")
        self.assertLess(dockerfile.index("COPY . /app"), export_at)
        self.assertLess(export_at, dockerfile.index("USER bot"), "exported as root: the bot can't change them")

    def test_only_the_deployment_files_are_let_into_the_build(self):
        ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
        for kept_out in (".env", "docker/compose.env", "*.sqlite3", "backups/", "logs/", "dev/"):
            self.assertIn(kept_out, ignore)
        allowed = {line[1:] for line in ignore if line.startswith("!")}
        self.assertEqual(allowed, {".env.example", "setup.bat", "start.bat", "update.bat", "stop.bat", "backup.bat",
                                   "restore.bat", "docker-compose.yml"})

    def test_no_container_gets_the_docker_socket_or_extra_privileges(self):
        for name in ("docker-compose.yml", "docker-compose.build.yml"):
            text = (ROOT / name).read_text(encoding="utf-8")
            for forbidden in ("docker.sock", "privileged", "cap_add", "network_mode: host", "pid: host"):
                self.assertNotIn(forbidden, text, f"{name}: {forbidden}")
