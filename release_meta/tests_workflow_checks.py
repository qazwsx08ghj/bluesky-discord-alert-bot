"""The checks the release workflow runs (docs/RELEASES.md, section 5): the compatibility file, the
withdrawal-list history in git, and the published release read back like an updater reads it."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from release_meta import MANIFEST_ASSET, REPOSITORY, MetadataError, generate, github
from release_meta.tests import make_manifest
from release_meta.tests_github import FakeGitHub

ROOT = Path(__file__).resolve().parent.parent
COMPAT = json.loads((ROOT / "release" / "compatibility.json").read_text(encoding="utf-8"))


class CompatibilityFileTests(unittest.TestCase):
    def test_the_committed_file_is_valid(self):
        self.assertEqual(generate.read_compatibility(COMPAT)["min_updater"], 1)

    def test_invalid_files(self):
        for change, text in (({"min_updater": 0}, "min_updater"), ({"min_updater": True}, "min_updater"),
                             ({"min_from_version": "1.0"}, "isn't a version"),
                             ({"stepping_stones": ["1.2.0", "1.1.0"]}, "in order"),
                             ({"stepping_stones": "1.1.0"}, "stepping_stones"),
                             ({"compose_min": "2.23"}, "compose_min"), ({"extra": 1}, "unknown fields")):
            with self.subTest(change=change), self.assertRaises(MetadataError) as ctx:
                generate.read_compatibility({**COMPAT, **change})
            self.assertIn(text, str(ctx.exception))
        missing = dict(COMPAT)
        del missing["compose_min"]
        with self.assertRaises(MetadataError):
            generate.read_compatibility(missing)


@unittest.skipIf(shutil.which("git") is None, "needs git")
class WithdrawnHistoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "test")
        (self.repo / "release").mkdir()

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True)

    def commit(self, revision, entries=(), updated="2026-12-01"):
        data = {"schema": "alertbot-withdrawn/1", "revision": revision, "updated_at": updated,
                "withdrawn": [{"version": v, "withdrawn_at": updated, "reason": "Bad.", "replacement": None}
                              for v in entries]}
        (self.repo / "release" / "withdrawn.json").write_text(json.dumps(data), encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-q", "-m", f"revision {revision}")

    def check(self):
        return generate.check_withdrawn_history("release/withdrawn.json", self.repo)

    def test_revisions_that_go_up_with_every_change(self):
        self.commit(1)
        self.commit(2, ["1.0.1"])
        self.commit(3, ["1.0.1", "1.0.2"])
        self.assertEqual(self.check(), [1, 2, 3])

    def test_a_change_without_a_new_revision(self):
        self.commit(1)
        self.commit(1, ["1.0.1"])
        with self.assertRaises(MetadataError) as ctx:
            self.check()
        self.assertIn("without a new revision", str(ctx.exception))

    def test_a_revision_that_goes_down(self):
        self.commit(2)
        self.commit(1, ["1.0.1"])
        with self.assertRaises(MetadataError) as ctx:
            self.check()
        self.assertIn("went back to revision 1", str(ctx.exception))

    def test_an_uncommitted_change_is_checked_too(self):
        self.commit(1)
        data = json.loads((self.repo / "release" / "withdrawn.json").read_text(encoding="utf-8"))
        data["withdrawn"] = [{"version": "1.0.1", "withdrawn_at": "2026-12-01", "reason": "Bad.", "replacement": None}]
        (self.repo / "release" / "withdrawn.json").write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(MetadataError):
            self.check()

    def test_reformatting_alone_needs_no_new_revision(self):
        self.commit(1)
        text = (self.repo / "release" / "withdrawn.json").read_text(encoding="utf-8")
        (self.repo / "release" / "withdrawn.json").write_text(json.dumps(json.loads(text), indent=4), encoding="utf-8")
        self.git("commit", "-q", "-am", "reformat")
        self.assertEqual(self.check(), [1, 1])

    def test_a_shallow_clone_is_refused(self):
        self.commit(1)
        self.commit(2, ["1.0.1"])
        shallow = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, shallow, True)
        subprocess.run(["git", "clone", "-q", "--depth", "1", self.repo.as_uri(), str(shallow / "c")], check=True,
                       capture_output=True)
        with self.assertRaises(MetadataError) as ctx:
            generate.check_withdrawn_history("release/withdrawn.json", shallow / "c")
        self.assertIn("full git history", str(ctx.exception))


class VerifyReleaseTests(unittest.TestCase):
    """The workflow's last check: the published release, read back from the public API."""

    def setUp(self):
        self.gh = FakeGitHub()
        self.addCleanup(self.gh.close)
        self.client = github.Client(api=self.gh.base, test_hosts=(self.gh.base + "/",))
        self.raw = json.dumps(make_manifest("1.2.0")).encode()

    def publish(self, immutable=True, raw=None):
        self.gh.raw("/dl/manifest", raw if raw is not None else self.raw)
        self.gh.json(f"/repos/{REPOSITORY}/releases/tags/v1.2.0", {
            "tag_name": "v1.2.0", "draft": False, "prerelease": False, "immutable": immutable,
            "assets": [{"name": MANIFEST_ASSET, "size": len(self.raw), "digest": None,
                        "browser_download_url": f"{self.gh.base}/dl/manifest"},
                       {"name": "BlueskyDiscordAlertBot-1.2.0.zip", "size": 4096, "digest": None,
                        "browser_download_url": f"{self.gh.base}/dl/zip"}]})

    def test_the_published_release_is_the_one_built(self):
        self.publish()
        m = github.verify_release(self.client, "v1.2.0", expected_manifest=self.raw)
        self.assertEqual(str(m.version), "1.2.0")

    def test_not_immutable(self):
        self.publish(immutable=False)
        with self.assertRaises(MetadataError) as ctx:
            github.verify_release(self.client, "v1.2.0", self.raw)
        self.assertIn("isn't an immutable release", str(ctx.exception))

    def test_a_different_manifest_was_published(self):
        other = json.dumps(make_manifest("1.2.0", **{"source.run_id": "42"})).encode()
        self.publish(raw=other)
        with self.assertRaises(MetadataError) as ctx:
            github.verify_release(self.client, "v1.2.0", self.raw)
        self.assertIn("isn't the one that was built", str(ctx.exception))

    def test_a_missing_release_or_a_bad_tag(self):
        with self.assertRaises(MetadataError):
            github.verify_release(self.client, "v1.2.0", self.raw)
        with self.assertRaises(MetadataError):
            github.verify_release(self.client, "1.2.0", self.raw)


if __name__ == "__main__":
    unittest.main()
