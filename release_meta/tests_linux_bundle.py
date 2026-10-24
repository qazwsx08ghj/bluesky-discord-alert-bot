"""The Linux deployment files and download (docker/make_release_bundle.py --platform linux), and how
the manifest describes them (release_meta/generate.py, manifest.py). No network, no Docker."""
import io
import json
import stat
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from release_meta import IMAGE, MetadataError, bundle, generate, manifest
from release_meta.tests_bundle import mrb
from release_meta.tests_generate import VERSION, GenerateTestCase

H = "f" * 64


class LinuxBundleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.folder = self.tmp / "linux"
        self.info = mrb.export(self.folder, platform="linux")

    def test_the_files_the_image_carries(self):
        self.assertEqual(sorted(self.info["files"]), ["LICENSE", "README.txt", "README.zh-TW.txt",
                                                       "THIRD_PARTY_NOTICES.txt", "alertbot", "docker-compose.yml"])
        self.assertEqual(self.info["platform"], "linux")
        if sys.platform != "win32":  # Windows has no executable bit; the image build checks it (test -x)
            self.assertTrue((self.folder / "alertbot").stat().st_mode & stat.S_IXUSR, "./alertbot can run")
        script = (self.folder / "alertbot").read_bytes()
        self.assertTrue(script.startswith(b"#!/usr/bin/env bash\n"))
        self.assertNotIn(b"\r\n", script, "LF line ends, whatever the checkout")
        sums = (self.tmp / "linux.sha256").read_text(encoding="ascii")
        self.assertIn(f"{self.info['files']['alertbot']} alertbot\n", sums)
        self.assertEqual(bundle.verify(self.folder, "linux"), self.info["files"])
        with self.assertRaises(MetadataError):
            bundle.verify(self.folder, "windows")  # each set says which platform it is for

    def test_the_download(self):
        image = f"{IMAGE}:{VERSION}@sha256:{'a' * 64}"
        path = mrb.make(image, self.tmp / "dist", from_bundle=self.folder, platform="linux")
        self.assertEqual(path.name, f"BlueskyDiscordAlertBot-{VERSION}-linux.tar.gz")
        files = generate.read_tar(path)
        self.assertEqual(files["docker/release.env"], f"ALERTBOT_IMAGE={image}\n".encode())
        self.assertNotIn("docker/compose.env", files, "unpacking again must not replace an install's settings")
        with tarfile.open(path) as tar:
            names = tar.getnames()
            self.assertTrue(all(n == "bluesky-alert-bot" or n.startswith("bluesky-alert-bot/") for n in names))
            self.assertEqual(tar.getmember("bluesky-alert-bot/alertbot").mode, 0o755)
            self.assertEqual(tar.getmember("bluesky-alert-bot/docker-compose.yml").mode, 0o644)
        again = mrb.make(image, self.tmp / "again", from_bundle=self.folder, platform="linux")
        self.assertEqual(path.read_bytes(), again.read_bytes(), "reproducible")

    def test_a_changed_file_is_refused(self):
        with open(self.folder / "alertbot", "ab") as f:
            f.write(b"# changed\n")
        with self.assertRaises(SystemExit):
            mrb.make(f"{IMAGE}:{VERSION}", self.tmp / "dist", from_bundle=self.folder, platform="linux")


class LinuxManifestTests(GenerateTestCase):
    def setUp(self):
        super().setUp()
        self.linux_folder = self.tmp / "image" / "linux"
        mrb.export(self.linux_folder, platform="linux")
        self.tar = mrb.make(f"{IMAGE}:{VERSION}@{self.digest}", self.out, from_bundle=self.linux_folder, platform="linux")
        self.describe["bundles"]["linux"] = {"files": bundle.verify(self.linux_folder, "linux")}

    def retar(self, change):
        files = generate.read_tar(self.tar)
        change(files)
        self.tar.unlink()
        return mrb.write_tar(self.tar, files)

    def test_both_downloads_in_the_manifest_and_checksums(self):
        data = self.gen(linux_bundle_path=self.tar)
        manifest.validate(data)
        linux = data["artifacts"]["linux-bundle"]
        self.assertEqual(linux["name"], self.tar.name)
        self.assertEqual(linux["size"], self.tar.stat().st_size)
        self.assertIn("docker/release.env", linux["files"])
        generate.write_release_files(self.out, data, self.zip, self.tar)
        self.assertEqual(sorted(generate.verify_checksums(self.out)),
                         sorted([self.zip.name, self.tar.name, "alertbot-release.json"]))

    def test_the_linux_download_must_come_with_the_image_files(self):
        self.assertRefused("must come together")  # the image has Linux files, the download is missing
        del self.describe["bundles"]["linux"]
        self.assertRefused("must come together", linux_bundle_path=self.tar)

    def test_inconsistent_linux_downloads_are_refused(self):
        self.retar(lambda f: f.__setitem__("docker/release.env", f"ALERTBOT_IMAGE={IMAGE}:{VERSION}\n".encode()))
        self.assertRefused("must pin this image by its digest", linux_bundle_path=self.tar)
        self.retar(lambda f: f.__setitem__("docker/release.env", f"ALERTBOT_IMAGE={IMAGE}@{self.digest}\n".encode()))
        self.retar(lambda f: f.__setitem__("alertbot", b"#!/bin/sh\necho changed\n"))
        self.assertRefused("alertbot in the Linux download differs", linux_bundle_path=self.tar)
        self.retar(lambda f: f.__setitem__("alertbot", (self.linux_folder / "alertbot").read_bytes()))
        self.retar(lambda f: f.__setitem__("docker/compose.env", b"ALERTBOT_PORT=1\n"))
        self.assertRefused("must not contain docker/compose.env", linux_bundle_path=self.tar)

    def test_links_and_unsafe_paths_are_refused(self):
        for name, kind in (("bluesky-alert-bot/evil", tarfile.SYMTYPE), ("../evil", tarfile.REGTYPE),
                           ("elsewhere/alertbot", tarfile.REGTYPE)):
            raw = io.BytesIO()
            with tarfile.open(fileobj=raw, mode="w") as tar:
                entry = tarfile.TarInfo(name)
                entry.type = kind
                entry.linkname = "/etc/passwd" if kind == tarfile.SYMTYPE else ""
                tar.addfile(entry, io.BytesIO(b"") if kind == tarfile.REGTYPE else None)
            import gzip
            self.tar.write_bytes(gzip.compress(raw.getvalue()))
            with self.assertRaises(MetadataError, msg=name):
                generate.read_tar(self.tar)

    def test_corrupt_or_oversized_archives_are_refused_cleanly(self):
        good = self.tar.read_bytes()
        self.tar.write_bytes(good[:20] + b"\x00" * 50 + good[70:])  # a broken deflate stream
        with self.assertRaises(MetadataError):
            generate.read_tar(self.tar)
        self.tar.write_bytes(good[: len(good) // 2])  # cut short
        with self.assertRaises(MetadataError):
            generate.read_tar(self.tar)
        self.tar.write_bytes(good)
        from unittest import mock
        with mock.patch.object(generate, "MAX_TAR_BYTES", 1024), self.assertRaises(MetadataError) as ctx:
            generate.read_tar(self.tar)
        self.assertIn("larger than allowed", str(ctx.exception))

    def test_the_manifest_checks_a_linux_download_completely(self):
        data = self.gen(linux_bundle_path=self.tar)
        data["artifacts"]["linux-bundle"]["name"] = "BlueskyDiscordAlertBot-9.9.9-linux.tar.gz"
        with self.assertRaises(MetadataError):
            manifest.validate(data)
        data = self.gen(linux_bundle_path=self.tar)
        del data["artifacts"]["linux-bundle"]["files"]["docker/release.env"]
        with self.assertRaises(MetadataError):
            manifest.validate(data)

    def test_an_unknown_kind_of_download_is_only_shape_checked(self):
        # How 1.0.0-rc.3's updater treats "linux-bundle": a later version may add downloads.
        data = self.gen(linux_bundle_path=self.tar)
        data["artifacts"]["other-bundle"] = {"name": "x.tar.gz", "sha256": H}
        manifest.validate(json.loads(json.dumps(data)))
