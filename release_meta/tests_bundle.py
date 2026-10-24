"""The deployment files inside the image (release_meta/bundle.py) and the download built from them
(docker/make_release_bundle.py). Plain unittest, no network."""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from release_meta import MetadataError, bundle, manifest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "docker"))
import make_release_bundle as mrb  # noqa: E402

EXPECTED = sorted(["setup.bat", "start.bat", "update.bat", "stop.bat", "backup.bat", "restore.bat",
                   "docker-compose.yml", "docker/scripts/common.bat", "README.txt", "README.zh-TW.txt", "LICENSE",
                   "THIRD_PARTY_NOTICES.txt"])


class BundleTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.folder = self.tmp / "windows"
        mrb.export(self.folder)

    def index(self):
        return json.loads((self.folder / "bundle.json").read_text(encoding="utf-8"))

    def write_index(self, data):
        (self.folder / "bundle.json").write_text(json.dumps(data), encoding="utf-8")

    def assertRefused(self, text):
        with self.assertRaises(MetadataError) as ctx:
            bundle.verify(self.folder)
        self.assertIn(text, str(ctx.exception))


class ExportTests(BundleTestCase):
    def test_exactly_the_deployment_files(self):
        self.assertEqual(sorted(bundle.verify(self.folder)), EXPECTED)
        self.assertTrue(set(manifest.WINDOWS_FILES) <= set(EXPECTED), "everything an updater installs")

    def test_nothing_local_or_secret_is_bundled(self):
        names = {p.relative_to(self.folder).as_posix() for p in self.folder.rglob("*")}
        for forbidden in ("docker/compose.env", ".env", "db.sqlite3", "backups", "logs", "local.env"):
            self.assertNotIn(forbidden, names)
        import re

        for name, data in mrb.bundle_files().items():
            text = data.decode("utf-8", "replace")
            self.assertNotRegex(text, r"discord(app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]{20,}", f"{name}: a webhook")
            self.assertNotRegex(text, r"(?m)^\s*(BLUESKY_PASSWORD|SECRET_KEY|DISCORD_WEBHOOK_URL)=\S", f"{name}: a secret")

    def test_line_endings_are_fixed(self):
        files = mrb.bundle_files()
        for name, data in files.items():
            if name.endswith(".bat"):
                self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), f"{name} is CRLF")
            elif name in ("docker-compose.yml", "LICENSE", "THIRD_PARTY_NOTICES.txt"):
                self.assertNotIn(b"\r\n", data, f"{name} is LF")
        self.assertTrue(files["README.txt"].startswith(b"\xef\xbb\xbf"), "a BOM, for Notepad")

    def test_the_index_matches_the_files(self):
        info = self.index()
        self.assertEqual(info["schema"], "alertbot-bundle/1")
        for name, digest in info["files"].items():
            self.assertEqual(hashlib.sha256((self.folder / name).read_bytes()).hexdigest(), digest)


class VerifyTests(BundleTestCase):
    def test_a_missing_file(self):
        (self.folder / "update.bat").unlink()
        self.assertRefused("missing ['update.bat']")

    def test_an_extra_file(self):
        (self.folder / "extra.bat").write_text("echo hi", encoding="ascii")
        self.assertRefused("unexpected ['extra.bat']")

    def test_a_changed_file(self):
        with open(self.folder / "docker" / "scripts" / "common.bat", "ab") as f:
            f.write(b"rem changed\r\n")
        self.assertRefused("docker/scripts/common.bat doesn't match its hash")

    def test_a_required_file_missing_from_index_and_disk(self):
        info = self.index()
        del info["files"]["update.bat"]
        self.write_index(info)
        (self.folder / "update.bat").unlink()
        self.assertRefused("lack update.bat")

    def test_compose_env_is_never_a_deployment_file(self):
        (self.folder / "docker" / "compose.env").write_text("ALERTBOT_IMAGE=x\n", encoding="utf-8")
        info = self.index()
        info["files"]["docker/compose.env"] = hashlib.sha256(b"ALERTBOT_IMAGE=x\n").hexdigest()
        self.write_index(info)
        self.assertRefused("must never be a deployment file")

    def test_malformed_indexes(self):
        good = self.index()
        cases = [
            ({**good, "schema": "alertbot-bundle/2"}, "unknown schema"),
            ({**good, "platform": "linux"}, "another platform"),
            ({**good, "files": {}}, "lists no files"),
            ({**good, "files": {**good["files"], "../evil.bat": "a" * 64}}, "unsafe path"),
            ({**good, "files": {**good["files"], "C:/evil.bat": "a" * 64}}, "unsafe path"),
            ({**good, "files": {**good["files"], "update.bat": "A" * 64}}, "malformed hash"),
            ({**good, "files": {**good["files"], "x.env": "a" * 64}}, "must never be a deployment file"),
        ]
        for data, text in cases:
            with self.subTest(text=text):
                self.write_index(data)
                self.assertRefused(text)
        (self.folder / "bundle.json").write_text("{broken", encoding="utf-8")
        self.assertRefused("isn't valid JSON")
        (self.folder / "bundle.json").unlink()
        self.assertRefused("have no bundle.json")


class DownloadTests(BundleTestCase):
    IMAGE = "ghcr.io/qazwsx08ghj/bluesky-discord-alert-bot:1.2.0@sha256:" + "1" * 64

    def zip_names(self, path):
        with zipfile.ZipFile(path) as zf:
            return sorted(zf.namelist())

    def test_the_zip_is_reproducible(self):
        a = mrb.make(self.IMAGE, self.tmp / "a")
        b = mrb.make(self.IMAGE, self.tmp / "b")
        c = mrb.make(self.IMAGE, self.tmp / "c", from_bundle=self.folder)
        self.assertEqual(a.read_bytes(), b.read_bytes(), "the same files give the same bytes")
        self.assertEqual(a.read_bytes(), c.read_bytes(), "from the image's files or the repository's: identical")
        self.assertEqual(a.name, "BlueskyDiscordAlertBot-1.2.0.zip")

    def test_the_zip_holds_the_bundle_plus_compose_env(self):
        z = mrb.make(self.IMAGE, self.tmp / "out", from_bundle=self.folder)
        self.assertEqual(self.zip_names(z), sorted(EXPECTED + ["docker/compose.env"]))
        with zipfile.ZipFile(z) as zf:
            env = zf.read("docker/compose.env").decode()
            info = zf.getinfo("update.bat")
        self.assertIn(f"ALERTBOT_IMAGE={self.IMAGE}\n", env)
        self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))

    def test_a_tampered_image_bundle_isnt_zipped(self):
        (self.folder / "start.bat").write_bytes(b"@echo off\r\necho changed\r\n")
        with self.assertRaises(SystemExit) as ctx:
            mrb.make(self.IMAGE, self.tmp / "out", from_bundle=self.folder)
        self.assertIn("start.bat doesn't match its hash", str(ctx.exception))
        self.assertFalse((self.tmp / "out").exists() and any((self.tmp / "out").iterdir()))

    def test_bad_image_names(self):
        for bad in ("not valid; rm -rf /", "UPPER/case:1", "ghcr.io/x/y:1.0.0@sha256:short"):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                mrb.make(bad, self.tmp / "out")


if __name__ == "__main__":
    unittest.main()
