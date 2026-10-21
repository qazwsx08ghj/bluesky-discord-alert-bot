"""THIRD_PARTY_NOTICES.txt stays complete and current (docker/third_party_notices.py).

The full check needs the image's Python packages (requirements-dev.txt) and, for the panel's
notices, frontend/node_modules (npm ci). CI runs it with both; the Docker build checks the
Python notices against the packages really installed in the image.
"""
import importlib.util
import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock, skipUnless

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)
NOTICES = ROOT / "THIRD_PARTY_NOTICES.txt"
_spec = importlib.util.spec_from_file_location("third_party_notices", ROOT / "docker" / "third_party_notices.py")
tpn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tpn)
HAS_NODE_MODULES = (ROOT / "frontend" / "node_modules" / "react" / "package.json").is_file()


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = tpn.main(list(args))
    return code, out.getvalue() + err.getvalue()


class NoticesFileTests(SimpleTestCase):
    def setUp(self):
        self.text = NOTICES.read_text(encoding="utf-8")

    def test_the_python_notices_match_the_installed_packages(self):
        code, out = run("--check", "--python-only")
        self.assertEqual(code, 0, out)

    @skipUnless(HAS_NODE_MODULES, "needs frontend/node_modules (npm ci)")
    def test_the_whole_file_is_up_to_date(self):
        code, out = run("--check")
        self.assertEqual(code, 0, out)

    def test_every_shipped_package_is_listed_with_its_exact_version(self):
        for name, version in tpn.pinned_python_packages().items():
            self.assertRegex(self.text, rf"(?im)^{name} {version}$", name)
        for _, name, version, _ in tpn.bundled_npm_packages():
            self.assertIn(f"\n{name} {version}\n", self.text, name)
        for required in ("Django", "react", "lucide-react", "@fontsource-variable/inter", "dnspython", "gunicorn"):
            self.assertRegex(self.text, rf"(?m)^{required} ", required)

    def test_the_notices_are_the_original_texts(self):
        self.assertIn("SIL OPEN FONT LICENSE Version 1.1", self.text, "the Inter font's license travels with it")
        self.assertIn("Copyright (c) Meta Platforms, Inc. and affiliates.", self.text)
        self.assertIn("Cole Bemis", self.text, "Lucide's notice includes Feather's")
        self.assertIn("Copyright (C) Dnspython Contributors", self.text)
        self.assertNotIn("\r", self.text)

    def test_development_tools_are_not_listed(self):
        for tool in ("drf-spectacular", "jsonschema", "PyYAML", "vite", "vitest", "typescript", "@playwright/test"):
            self.assertNotRegex(self.text, rf"(?m)^{tool} ", tool)

    def test_every_override_is_used(self):
        pins = {name.lower(): version for name, version in tpn.pinned_python_packages().items()}
        for path in (ROOT / "docker" / "notices").glob("*.txt"):
            name, version = path.stem.rsplit("-", 1)
            self.assertEqual(pins.get(name.lower()), version, f"{path.name} is for a version that isn't shipped")


class NoticesCheckTests(SimpleTestCase):
    """The check notices what goes wrong when dependencies change."""

    def setUp(self):
        self.text = NOTICES.read_text(encoding="utf-8")

    def check_python_only(self, text):
        with self.assertRaises(tpn.NoticeError) as ctx:
            tpn.check_python_only(text)
        return str(ctx.exception)

    def test_a_changed_or_missing_python_notice_is_caught(self):
        changed = self.text.replace("Django 5.2.18\nLicense: BSD-3-Clause", "Django 5.2.18\nLicense: Unknown")
        self.assertIn("Django 5.2.18 is missing or out of date", self.check_python_only(changed))
        self.assertIn("missing or out of date", self.check_python_only(self.text.replace("\nDjango 5.2.18\n", "\n")))

    def test_a_new_python_dependency_without_a_notice_is_caught(self):
        pins = {**tpn.pinned_python_packages(), "sqlparse": "9.9.9"}
        with mock.patch.object(tpn, "pinned_python_packages", return_value=pins):
            with self.assertRaises(tpn.NoticeError) as ctx:
                tpn.python_entries()
        self.assertIn("lock file says 9.9.9", str(ctx.exception))

    def test_a_package_without_a_license_file_needs_an_override(self):
        with mock.patch.object(tpn, "OVERRIDES", ROOT / "no-such-folder"):
            with self.assertRaises(tpn.NoticeError) as ctx:
                tpn.python_entries()
        self.assertIn("docker/notices/dnspython-2.9.0.txt", str(ctx.exception))

    def test_a_changed_panel_dependency_is_caught_even_without_node_modules(self):
        packages = [(path, name, "99.0.0" if name == "react" else version, lic)
                    for path, name, version, lic in tpn.bundled_npm_packages()]
        with mock.patch.object(tpn, "bundled_npm_packages", return_value=packages):
            self.assertIn("package-lock.json", self.check_python_only(self.text))

    def test_the_command_says_how_to_fix_it(self):
        with mock.patch.object(tpn, "OUTPUT", ROOT / "no-such-file.txt"):
            code, out = run("--check", "--python-only")
        self.assertEqual(code, 1)
        self.assertIn("missing or out of date", out)


class NoticesShippedTests(SimpleTestCase):
    """The notices go everywhere the software goes."""

    def test_the_docker_image_checks_and_carries_them(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY THIRD_PARTY_NOTICES.txt /THIRD_PARTY_NOTICES.txt", dockerfile, "the panel build gets it")
        self.assertIn("test -f dist/THIRD_PARTY_NOTICES.txt", dockerfile)
        self.assertIn("python docker/third_party_notices.py --check --python-only", dockerfile,
                      "the build fails if the notices don't match the installed packages")
        ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
        for name in ("THIRD_PARTY_NOTICES.txt", "LICENSE", "docker/", "docker/notices/"):
            self.assertNotIn(name, ignored)

    def test_the_user_download_includes_them(self):
        bundle = (ROOT / "docker" / "make_release_bundle.py").read_text(encoding="utf-8")
        self.assertIn('"THIRD_PARTY_NOTICES.txt"', bundle)
        self.assertIn('"LICENSE"', bundle)

    def test_ci_runs_the_full_check(self):
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("python docker/third_party_notices.py --check", ci)
