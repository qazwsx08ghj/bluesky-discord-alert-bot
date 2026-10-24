"""Building the manifest and checksums from build outputs (release_meta/generate.py). No network,
no Docker: the image index and the image's description are built the way the release workflow
gets them (`imagetools inspect --raw`, `describe`). The real thing: config/tests_docker_release.py."""
import copy
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from release_meta import IMAGE, MANIFEST_ASSET, MetadataError, bundle, generate, manifest
from release_meta.tests_bundle import mrb

ROOT = Path(__file__).resolve().parent.parent
VERSION = "1.2.0"
COMPAT = json.loads((ROOT / "release" / "compatibility.json").read_text(encoding="utf-8"))
DATA_SCHEMA = {"activity": "0001_initial", "bluesky_monitor": "0009_remove_single_channel_fields",
               "botconfig": "0002_remove_legacy_copies", "discord_alerts": "0003_channel_guild_id"}


def make_index(*platforms, extra=()):
    """Raw bytes of an OCI image index, like buildx pushes (with an attestation entry), and its digest."""
    entries = [{"mediaType": "application/vnd.oci.image.manifest.v1+json", "size": 1234,
                "digest": "sha256:" + hashlib.sha256(p.encode()).hexdigest(),
                "platform": {"os": p.split("/")[0], "architecture": p.split("/")[1]}} for p in platforms]
    entries.append({"mediaType": "application/vnd.oci.image.manifest.v1+json", "size": 800,
                    "digest": "sha256:" + "e" * 64, "platform": {"os": "unknown", "architecture": "unknown"},
                    "annotations": {"vnd.docker.reference.type": "attestation-manifest"}})
    entries += list(extra)
    raw = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
                      "manifests": entries}).encode()
    return raw, "sha256:" + hashlib.sha256(raw).hexdigest()


class GenerateTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.folder = self.tmp / "image" / "windows"
        mrb.export(self.folder)  # what `docker cp <image>:/app/bundle/windows` gives
        self.index_raw, self.digest = make_index("linux/amd64", "linux/arm64")
        self.out = self.tmp / "dist"
        self.zip = mrb.make(f"{IMAGE}:{VERSION}@{self.digest}", self.out, from_bundle=self.folder)
        self.describe = {"schema": "alertbot-describe/1", "version": VERSION, "version_is_release": True,
                         "platform": "linux/amd64", "data_schema": dict(DATA_SCHEMA), "problems": [],
                         "bundles": {"windows": {"files": bundle.verify(self.folder)}}}

    def gen(self, **changes):
        args = dict(version=VERSION, commit="c" * 40, run_id="987654", index_digest=self.digest,
                    index_raw=self.index_raw, describe=self.describe, bundle_path=self.zip, compatibility=COMPAT)
        args.update(changes)
        return generate.generate(**args)

    def assertRefused(self, text, **changes):
        with self.assertRaises(MetadataError) as ctx:
            self.gen(**changes)
        self.assertIn(text, str(ctx.exception))

    def rezip(self, change):
        """The download, changed by change(files) (a dict of name -> bytes)."""
        with zipfile.ZipFile(self.zip) as zf:
            files = {n: zf.read(n) for n in zf.namelist()}
        change(files)
        self.zip.unlink()
        return mrb.write_zip(self.zip, files)


class GenerateTests(GenerateTestCase):
    def test_a_release_manifest_from_the_build(self):
        data = self.gen()
        m = manifest.validate(data)
        self.assertEqual(m.image_reference, f"{IMAGE}@{self.digest}", "pulled by the index digest")
        self.assertEqual(data["image"]["platforms"], {"linux/amd64": "sha256:" + hashlib.sha256(b"linux/amd64").hexdigest(),
                                                     "linux/arm64": "sha256:" + hashlib.sha256(b"linux/arm64").hexdigest()},
                         "from the index, without the attestation entry")
        self.assertEqual(data["compatibility"]["data_schema"], DATA_SCHEMA, "from the image itself")
        self.assertEqual(data["compatibility"]["min_from_version"], COMPAT["min_from_version"])
        bundle_entry = data["artifacts"]["windows-bundle"]
        self.assertEqual(bundle_entry["sha256"], hashlib.sha256(self.zip.read_bytes()).hexdigest())
        self.assertEqual(bundle_entry["size"], self.zip.stat().st_size)
        self.assertIn("docker/compose.env", bundle_entry["files"])
        self.assertEqual(data["channel"], "stable")
        self.assertEqual(self.gen(version="1.2.0-rc.1", describe={**self.describe, "version": "1.2.0-rc.1"},
                                  bundle_path=mrb.make(f"{IMAGE}:1.2.0-rc.1@{self.digest}", self.tmp / "rc",
                                                       from_bundle=self.folder))["channel"], "prerelease")

    def test_reproducible(self):
        self.assertEqual(generate.dump(self.gen()), generate.dump(self.gen()))
        again = mrb.make(f"{IMAGE}:{VERSION}@{self.digest}", self.tmp / "again", from_bundle=self.folder)
        self.assertEqual(again.read_bytes(), self.zip.read_bytes())

    def test_files_and_checksums(self):
        manifest_path, sums = generate.write_release_files(self.out, self.gen(), self.zip)
        self.assertEqual(manifest_path.name, MANIFEST_ASSET)
        self.assertEqual(generate.verify_checksums(self.out), sorted([MANIFEST_ASSET, self.zip.name]))
        lines = sums.read_text(encoding="utf-8").splitlines()
        self.assertEqual([l.split("  ")[1] for l in lines], sorted([MANIFEST_ASSET, self.zip.name]))
        self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8")), self.gen())

    # -- the image index -----------------------------------------------------------------------------
    def test_the_index_must_match_the_digest(self):
        other_raw, _ = make_index("linux/amd64")
        self.assertRefused("doesn't match the image digest", index_raw=other_raw)
        self.assertRefused("malformed", index_digest="sha256:short")
        self.assertRefused("malformed", index_digest=self.digest.replace("sha256:", "sha512:"))

    def test_the_digest_must_be_a_multi_platform_index(self):
        single = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json"}).encode()
        self.assertRefused("isn't a multi-platform image index", index_raw=single,
                           index_digest="sha256:" + hashlib.sha256(single).hexdigest())

    def test_platform_problems(self):
        raw, digest = make_index("linux/arm64")
        self.assertRefused("no linux/amd64", index_raw=raw, index_digest=digest)
        raw, digest = make_index("linux/amd64", "linux/amd64")
        self.assertRefused("lists linux/amd64 twice", index_raw=raw, index_digest=digest)
        raw, digest = make_index("linux/amd64", extra=[{"digest": "latest", "platform": {"os": "linux", "architecture": "arm64"}}])
        self.assertRefused("malformed digest for linux/arm64", index_raw=raw, index_digest=digest)

    # -- what the image says about itself --------------------------------------------------------------
    def test_the_image_must_be_this_version(self):
        self.assertRefused("says it is 'dev', not 1.2.0", describe={**self.describe, "version": "dev"})
        self.assertRefused("says it is '1.2.1'", describe={**self.describe, "version": "1.2.1"})
        self.assertRefused("isn't a version", version="1.2")

    def test_the_image_must_be_complete(self):
        self.assertRefused("reports problems", describe={**self.describe, "problems": ["windows deployment files: x"]})
        self.assertRefused("isn't from describe_release", describe={**self.describe, "schema": "other"})
        self.assertRefused("lists no deployment files", describe={**self.describe, "bundles": {}})
        self.assertRefused("isn't one of its platforms", describe={**self.describe, "platform": "linux/s390x"})

    # -- the download ----------------------------------------------------------------------------------
    def test_the_download_must_pin_this_image_by_digest(self):
        for pin in (f"{IMAGE}:{VERSION}", f"{IMAGE}:stable", f"ghcr.io/someone/else:{VERSION}@{self.digest}",
                    f"{IMAGE}:{VERSION}@sha256:" + "f" * 64):
            with self.subTest(pin=pin):
                self.rezip(lambda f: f.__setitem__("docker/compose.env", f"ALERTBOT_IMAGE={pin}\n".encode()))
                self.assertRefused("must pin this image by its digest")
        self.rezip(lambda f: f.__setitem__("docker/compose.env",
                                           f"ALERTBOT_IMAGE={IMAGE}@{self.digest}\nALERTBOT_IMAGE=x\n".encode()))
        self.assertRefused("must pin this image by its digest")
        self.rezip(lambda f: f.pop("docker/compose.env"))
        self.assertRefused("has no docker/compose.env")

    def test_the_download_must_hold_exactly_the_images_files(self):
        self.rezip(lambda f: f.pop("restore.bat"))
        self.assertRefused("missing ['restore.bat']")

    def test_an_extra_file_in_the_download(self):
        self.rezip(lambda f: f.__setitem__("extra.txt", b"x"))
        self.assertRefused("unexpected ['extra.txt']")

    def test_a_changed_file_in_the_download(self):
        self.rezip(lambda f: f.__setitem__("update.bat", f["update.bat"] + b"rem changed\r\n"))
        self.assertRefused("update.bat in the download differs from the image's")

    def test_no_env_file_ever(self):
        self.rezip(lambda f: f.__setitem__(".env", b"BLUESKY_PASSWORD=x\n"))
        self.assertRefused("unexpected ['.env']")

    def test_unsafe_or_broken_downloads(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../evil.bat", b"x")
        self.zip.write_bytes(buf.getvalue())
        self.assertRefused("unsafe path")
        self.zip.write_bytes(b"not a zip")
        self.assertRefused("isn't a readable zip")

    def test_the_download_name(self):
        renamed = self.zip.with_name("BlueskyDiscordAlertBot-stable.zip")
        self.zip.rename(renamed)
        self.assertRefused("must be named BlueskyDiscordAlertBot-1.2.0.zip", bundle_path=renamed)

    # -- compatibility policy --------------------------------------------------------------------------
    def test_the_compatibility_file(self):
        self.assertRefused("unknown schema", compatibility={**COMPAT, "schema": "x"})
        self.assertRefused("unknown fields", compatibility={**COMPAT, "min_from_versoin": "1.0.0"})
        self.assertRefused("newer than the version itself", compatibility={**COMPAT, "min_from_version": "2.0.0"})
        self.assertRefused("min_updater must be a whole number, 1 or more", compatibility={**COMPAT, "min_updater": 0})


class ChecksumTests(GenerateTestCase):
    def setUp(self):
        super().setUp()
        generate.write_release_files(self.out, self.gen(), self.zip)
        self.sums = self.out / "SHA256SUMS"

    def assertBad(self, text):
        with self.assertRaises(MetadataError) as ctx:
            generate.verify_checksums(self.out)
        self.assertIn(text, str(ctx.exception))

    def test_a_changed_file(self):
        with open(self.out / MANIFEST_ASSET, "ab") as f:
            f.write(b" ")
        self.assertBad("alertbot-release.json doesn't match SHA256SUMS")

    def test_a_missing_file(self):
        self.zip.unlink()
        self.assertBad("is listed in SHA256SUMS but missing")

    def test_malformed_lists(self):
        good = self.sums.read_text(encoding="utf-8")
        for text, why in (("x" * 64 + "  file\n", "malformed"), (good.splitlines()[0] + "\n" + good, "twice"),
                          ("", "lists nothing"), ("a" * 64 + "  ../file\n", "malformed"),
                          ("a" * 64 + " file\n", "malformed")):
            with self.subTest(why=why):
                self.sums.write_text(text, encoding="utf-8")
                self.assertBad(why)

    def test_command_line(self):
        res = subprocess.run([sys.executable, "-m", "release_meta", "verify-checksums", str(self.out)], cwd=ROOT,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("checksums match", res.stdout)


class GenerateCommandLineTests(GenerateTestCase):
    def test_generate_end_to_end(self):
        (self.tmp / "index.json").write_bytes(self.index_raw)
        (self.tmp / "describe.json").write_text(json.dumps(self.describe), encoding="utf-8")
        args = ["generate", "--version", VERSION, "--commit", "c" * 40, "--run-id", "1", "--index-digest", self.digest,
                "--index", str(self.tmp / "index.json"), "--describe", str(self.tmp / "describe.json"),
                "--bundle", str(self.zip), "--compatibility", str(ROOT / "release" / "compatibility.json"),
                "--out", str(self.out)]
        res = subprocess.run([sys.executable, "-m", "release_meta", *args], cwd=ROOT, capture_output=True, text=True,
                             timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue((self.out / MANIFEST_ASSET).is_file() and (self.out / "SHA256SUMS").is_file())
        res = subprocess.run([sys.executable, "-m", "release_meta", "validate-manifest", str(self.out / MANIFEST_ASSET)],
                             cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        bad = copy.deepcopy(self.describe)
        bad["version"] = "9.9.9"
        (self.tmp / "describe.json").write_text(json.dumps(bad), encoding="utf-8")
        res = subprocess.run([sys.executable, "-m", "release_meta", *args], cwd=ROOT, capture_output=True, text=True,
                             timeout=60)
        self.assertEqual(res.returncode, 1)
        self.assertIn("says it is '9.9.9'", res.stderr)


if __name__ == "__main__":
    unittest.main()
