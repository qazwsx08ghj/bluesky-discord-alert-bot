"""Release metadata rules (no network). Plain unittest: also runs without Django,
python -m unittest release_meta.tests release_meta.tests_github
"""
import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

from release_meta import IMAGE, REPOSITORY, UPDATER_PROTOCOL, MetadataError, manifest, select, semver, withdrawn

ROOT = Path(__file__).resolve().parent.parent
H = "a" * 64


def make_manifest(_version="1.0.0", **changes) -> dict:
    """A valid manifest for `version`; changes are dotted paths, e.g. {"compatibility.min_updater": 2}."""
    version = _version
    v = semver.parse(version)
    data = {
        "schema": "alertbot-release/1",
        "version": version,
        "channel": "prerelease" if v.is_prerelease else "stable",
        "source": {"repository": REPOSITORY, "commit": "c" * 40, "run_id": "123456"},
        "image": {"name": IMAGE, "digest": "sha256:" + "1" * 64,
                  "platforms": {"linux/amd64": "sha256:" + "2" * 64, "linux/arm64": "sha256:" + "3" * 64}},
        "compatibility": {"min_updater": 1, "min_from_version": "1.0.0-rc.1", "stepping_stones": [],
                          "data_schema": {"bluesky_monitor": "0009_remove_single_channel_fields",
                                          "discord_alerts": "0003_channel_guild_id"},
                          "compose_min": "2.23.0"},
        "artifacts": {"windows-bundle": {"name": f"BlueskyDiscordAlertBot-{version}.zip", "sha256": H, "size": 4096,
                                         "files": {n: H for n in manifest.WINDOWS_FILES}}},
        "notes_url": f"https://github.com/{REPOSITORY}/releases/tag/v{version}",
    }
    if semver.parse(data["compatibility"]["min_from_version"]) > v:
        data["compatibility"]["min_from_version"] = version
    for path, value in changes.items():
        *parents, last = path.split(".")
        node = data
        for p in parents:
            node = node[p]
        if value is DELETE:
            del node[last]
        else:
            node[last] = value
    return data


DELETE = object()


def candidate(version, **changes):
    return select.Candidate(semver.parse(version), manifest=manifest.validate(make_manifest(version, **changes)))


def withdrawn_list(*entries, revision=1):
    return withdrawn.validate({"schema": "alertbot-withdrawn/1", "revision": revision, "updated_at": "2026-12-31",
                               "withdrawn": [dict(e) for e in entries]})


NONE_WITHDRAWN = withdrawn_list()


class VersionTests(unittest.TestCase):
    def test_order(self):
        order = ["0.9.9", "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta.2", "1.0.0-beta.11",
                 "1.0.0-rc.1", "1.0.0-rc.2", "1.0.0-rc.10", "1.0.0", "1.0.1-rc.1", "1.0.1", "1.10.0", "2.0.0"]
        parsed = [semver.parse(v) for v in order]
        self.assertEqual(parsed, sorted(reversed(parsed)))
        self.assertEqual([str(p) for p in parsed], order, "printed back exactly")

    def test_refused(self):
        for bad in ("1.0", "v1.0.0", "01.0.0", "1.00.0", "1.0.0-rc.01", "1.0.0+build.1", "1.0.0-", "1.0.0-rc..1",
                    "", " 1.0.0", "1.0.0 ", "1.0.0-rc.1\n", None, 100, "1.0.0-ä"):
            with self.subTest(bad=bad), self.assertRaises(MetadataError):
                semver.parse(bad)

    def test_equal_versions_are_equal(self):
        self.assertEqual(semver.parse("1.0.0-rc.1"), semver.parse("1.0.0-rc.1"))
        self.assertTrue(semver.parse("1.0.0-rc.1").is_prerelease)
        self.assertFalse(semver.parse("1.0.0").is_prerelease)


class ManifestTests(unittest.TestCase):
    def test_a_valid_manifest(self):
        m = manifest.validate(make_manifest("1.2.0"))
        self.assertEqual(str(m.version), "1.2.0")
        self.assertEqual(m.image_reference, f"{IMAGE}@sha256:{'1' * 64}", "pulled by digest, never by tag")
        self.assertEqual(manifest.validate(make_manifest("1.2.0-rc.3")).channel, "prerelease")

    def test_unknown_fields_are_ignored(self):
        manifest.validate(make_manifest(**{"future_field": {"x": 1}, "compatibility.future": True}))

    def assertInvalid(self, data, text=""):
        with self.assertRaises(MetadataError) as ctx:
            manifest.validate(data)
        self.assertIn(text, str(ctx.exception))

    def test_every_required_field(self):
        for path in ("schema", "version", "channel", "source", "source.repository", "source.commit",
                     "source.run_id", "image", "image.name", "image.digest", "image.platforms",
                     "compatibility", "compatibility.min_updater", "compatibility.min_from_version",
                     "compatibility.stepping_stones", "compatibility.data_schema", "compatibility.compose_min",
                     "artifacts", "artifacts.windows-bundle.name", "artifacts.windows-bundle.sha256",
                     "artifacts.windows-bundle.size", "artifacts.windows-bundle.files", "notes_url"):
            with self.subTest(missing=path):
                self.assertInvalid(make_manifest(**{path: DELETE}))

    def test_wrong_types(self):
        for path, value in (("compatibility.min_updater", "1"), ("compatibility.min_updater", True),
                            ("compatibility.min_updater", 0), ("artifacts.windows-bundle.size", 0),
                            ("compatibility.stepping_stones", "1.0.0"), ("image.platforms", []),
                            ("source", "x"), ("artifacts", {}), ("compatibility.data_schema", {})):
            with self.subTest(path=path, value=value):
                self.assertInvalid(make_manifest(**{path: value}))

    def test_schema(self):
        self.assertInvalid(make_manifest(schema="alertbot-release/2"), "a newer updater is needed")
        self.assertInvalid(make_manifest(schema="something-else/1"), "isn't a release manifest")
        self.assertInvalid([], "isn't a JSON object")

    def test_channel_must_match_the_version(self):
        self.assertInvalid(make_manifest("1.0.0", channel="prerelease"), "can't be on the prerelease channel")
        self.assertInvalid(make_manifest("1.0.0-rc.1", channel="stable"), "can't be on the stable channel")
        self.assertInvalid(make_manifest(channel="beta"), "unknown channel")

    def test_identity_is_fixed(self):
        self.assertInvalid(make_manifest(**{"source.repository": "someone/else"}), "another repository")
        self.assertInvalid(make_manifest(**{"image.name": "ghcr.io/someone/else"}), "another image")
        self.assertInvalid(make_manifest(**{"image.name": "docker.io/" + REPOSITORY}), "another image")

    def test_digests_and_hashes(self):
        for path, value in (("image.digest", "sha256:" + "1" * 63), ("image.digest", "1" * 64),
                            ("image.digest", "sha256:" + "A" * 64), ("image.digest", "sha512:" + "1" * 64),
                            ("image.platforms", {"linux/arm64": "sha256:" + "3" * 64}),
                            ("image.platforms", {"linux/amd64": "sha256:" + "1" * 64}),
                            ("artifacts.windows-bundle.sha256", "z" * 64), ("source.commit", "c" * 39),
                            ("source.run_id", "12a"), ("image.digest", "sha256:" + "1" * 64 + "\n"),
                            ("artifacts.windows-bundle.sha256", H + "\n"), ("source.commit", "c" * 40 + "\n")):
            with self.subTest(path=path, value=value):
                self.assertInvalid(make_manifest(**{path: value}))

    def test_compatibility_rules(self):
        self.assertInvalid(make_manifest("1.0.0", **{"compatibility.min_from_version": "1.1.0"}), "newer than")
        self.assertInvalid(make_manifest("1.2.0", **{"compatibility.stepping_stones": ["1.1.0", "1.0.0"]}), "order")
        self.assertInvalid(make_manifest("1.2.0", **{"compatibility.stepping_stones": ["1.1.0", "1.1.0"]}), "repeats")
        self.assertInvalid(make_manifest("1.2.0", **{"compatibility.stepping_stones": ["1.2.0"]}), "older than")
        self.assertInvalid(make_manifest(**{"compatibility.compose_min": "2.23"}))
        self.assertInvalid(make_manifest(**{"compatibility.data_schema": {"Bad-App": "0001_x"}}))
        self.assertInvalid(make_manifest(**{"compatibility.data_schema": {"app": "initial"}}))

    def test_the_download(self):
        self.assertInvalid(make_manifest(**{"artifacts.windows-bundle.name": "BlueskyDiscordAlertBot-stable.zip"}),
                           "doesn't match the version")
        files = {n: H for n in manifest.WINDOWS_FILES if n != "update.bat"}
        self.assertInvalid(make_manifest(**{"artifacts.windows-bundle.files": files}), "missing update.bat")
        for unsafe in ("../evil.bat", "/etc/passwd", "docker/../../x", "\\x.bat"):
            with self.subTest(path=unsafe):
                files = {**{n: H for n in manifest.WINDOWS_FILES}, unsafe: H}
                self.assertInvalid(make_manifest(**{"artifacts.windows-bundle.files": files}), "unsafe path")

    def test_notes_url(self):
        self.assertInvalid(make_manifest(notes_url="https://example.com/notes"), "notes_url")


class ReleaseRecordTests(unittest.TestCase):
    def record(self, version="1.0.0", **changes):
        data = make_manifest(version)
        assets = {"alertbot-release.json": {"size": 900, "digest": None, "url": "u"},
                  f"BlueskyDiscordAlertBot-{version}.zip": {"size": 4096, "digest": f"sha256:{H}", "url": "u"}}
        fields = dict(tag=f"v{version}", draft=False, prerelease=semver.parse(version).is_prerelease,
                      immutable=True, assets=assets)
        fields.update(changes)
        return manifest.validate(data), manifest.ReleaseRecord(**fields)

    def test_a_matching_release(self):
        manifest.check_against_release(*self.record())
        manifest.check_against_release(*self.record("1.0.0-rc.2"))

    def assertMismatch(self, text, **changes):
        m, r = self.record(**changes)
        with self.assertRaises(MetadataError) as ctx:
            manifest.check_against_release(m, r)
        self.assertIn(text, str(ctx.exception))

    def test_mismatches(self):
        self.assertMismatch("attached to v1.0.1", tag="v1.0.1")
        self.assertMismatch("pre-release flag", prerelease=True)
        self.assertMismatch("is a draft", draft=True)
        self.assertMismatch("isn't an immutable release", immutable=False)
        self.assertMismatch("no alertbot-release.json",
                            assets={"BlueskyDiscordAlertBot-1.0.0.zip": {"size": 4096, "digest": None, "url": "u"}})
        self.assertMismatch("is missing BlueskyDiscordAlertBot-1.0.0.zip",
                            assets={"alertbot-release.json": {"size": 1, "digest": None, "url": "u"}})
        self.assertMismatch("different size", assets={
            "alertbot-release.json": {"size": 1, "digest": None, "url": "u"},
            "BlueskyDiscordAlertBot-1.0.0.zip": {"size": 4097, "digest": None, "url": "u"}})
        self.assertMismatch("different hash", assets={
            "alertbot-release.json": {"size": 1, "digest": None, "url": "u"},
            "BlueskyDiscordAlertBot-1.0.0.zip": {"size": 4096, "digest": "sha256:" + "b" * 64, "url": "u"}})

    def test_immutability_can_only_be_waived_explicitly(self):
        m, r = self.record(immutable=False)
        manifest.check_against_release(m, r, require_immutable=False)


class WithdrawnTests(unittest.TestCase):
    def test_the_published_file_is_valid(self):
        data = json.loads((ROOT / "release" / "withdrawn.json").read_text(encoding="utf-8"))
        w = withdrawn.validate(data)
        self.assertGreaterEqual(w.revision, 1)

    def test_a_valid_list(self):
        w = withdrawn_list({"version": "1.0.1", "withdrawn_at": "2026-11-02", "reason": "Breaks channels.",
                            "replacement": "1.0.2"}, {"version": "1.1.0-rc.1", "withdrawn_at": "2026-11-03",
                                                      "reason": "Test build.", "replacement": None}, revision=4)
        self.assertIn(semver.parse("1.0.1"), w)
        self.assertEqual(w.get(semver.parse("1.0.1")).replacement, semver.parse("1.0.2"))
        self.assertEqual(w.revision, 4)

    def entry(self, **changes):
        e = {"version": "1.0.1", "withdrawn_at": "2026-11-02", "reason": "Breaks channels.", "replacement": "1.0.2"}
        e.update(changes)
        return e

    def assertInvalid(self, data, text=""):
        with self.assertRaises(MetadataError) as ctx:
            withdrawn.validate(data)
        self.assertIn(text, str(ctx.exception))

    def test_invalid_files(self):
        base = {"schema": "alertbot-withdrawn/1", "revision": 1, "updated_at": "2026-12-31", "withdrawn": []}
        for changes, text in (({"schema": "alertbot-withdrawn/2"}, "unknown schema"), ({"revision": 0}, "revision"),
                              ({"revision": "1"}, "revision"), ({"revision": True}, "revision"),
                              ({"updated_at": "31/12/2026"}, "updated_at"), ({"updated_at": "2026-12-31\n"}, "updated_at"),
                              ({"withdrawn": {}}, "must be a list")):
            with self.subTest(changes=changes):
                self.assertInvalid({**base, **changes}, text)
        self.assertInvalid([], "isn't a JSON object")
        for entry, text in ((self.entry(version="1.0"), "isn't a version"),
                            (self.entry(withdrawn_at="soon"), "withdrawn_at"),
                            (self.entry(withdrawn_at="2027-01-01"), "after the file's updated_at"),
                            (self.entry(reason=""), "reason"), (self.entry(reason="x" * 301), "reason"),
                            (self.entry(replacement="1.0.0"), "must be newer"),
                            (self.entry(replacement="1.0.1"), "must be newer"), ("text", "isn't an object")):
            with self.subTest(entry=entry):
                self.assertInvalid({**base, "withdrawn": [entry]}, text)
        self.assertInvalid({**base, "withdrawn": [self.entry(), self.entry()]}, "listed twice")
        self.assertInvalid({**base, "withdrawn": [self.entry(), self.entry(version="1.0.2", replacement=None)]},
                           "is withdrawn too")


class DecideTests(unittest.TestCase):
    def decide(self, installed, channel, *candidates, withdrawn_=NONE_WITHDRAWN, **kw):
        return select.decide(installed, channel, list(candidates), withdrawn_, **kw)

    def test_up_to_date(self):
        d = self.decide("1.1.0", "stable", candidate("1.0.0"), candidate("1.1.0"))
        self.assertEqual((d.action, d.steps), ("up_to_date", []))
        self.assertIn("You have the newest version (1.1.0)", d.message)

    def test_newest_on_the_channel(self):
        rels = (candidate("1.0.0"), candidate("1.1.0"), candidate("1.2.0-rc.1"))
        self.assertEqual(str(self.decide("1.0.0", "stable", *rels).target.version), "1.1.0")
        self.assertEqual(str(self.decide("1.0.0", "prerelease", *rels).target.version), "1.2.0-rc.1")
        self.assertEqual(self.decide("1.1.0", "stable", *rels).action, "up_to_date", "stable never gets an rc")

    def test_an_rc_install_moves_to_the_final_release(self):
        d = self.decide("1.0.0-rc.2", "prerelease", candidate("1.0.0-rc.2"), candidate("1.0.0"))
        self.assertEqual(str(d.target.version), "1.0.0")
        d = self.decide("1.0.0-rc.2", "stable", candidate("1.0.0-rc.3"), candidate("1.0.0"))
        self.assertEqual(str(d.target.version), "1.0.0", "a stable-channel rc install waits for the final release")

    def test_never_older(self):
        for installed in ("2.0.0", "1.1.0"):
            with self.subTest(installed=installed):
                d = self.decide(installed, "prerelease", candidate("1.0.0"), candidate("1.1.0"))
                self.assertEqual(d.action, "up_to_date")
        d = self.decide("1.0.0", "stable", candidate("1.0.0-rc.9"))
        self.assertEqual(d.action, "up_to_date", "an rc of the same version is older than the final release")

    def test_the_installed_version_must_be_a_version(self):
        for installed in ("dev", "", "ci", "1.0", None):
            with self.subTest(installed=installed):
                d = self.decide(installed, "stable", candidate("1.0.0"))
                self.assertEqual((d.action, d.reason), ("blocked", "installed_unknown"))
                self.assertIn("Nothing was changed.", d.message)

    def test_unknown_channel(self):
        self.assertEqual(self.decide("1.0.0", "beta", candidate("1.1.0")).reason, "channel_unknown")

    def test_a_withdrawn_release_is_skipped(self):
        w = withdrawn_list({"version": "1.2.0", "withdrawn_at": "2026-11-02", "reason": "Breaks.", "replacement": None})
        d = self.decide("1.0.0", "stable", candidate("1.1.0"), candidate("1.2.0"), withdrawn_=w)
        self.assertEqual(str(d.target.version), "1.1.0")

    def test_withdrawn_newest_with_nothing_else_is_up_to_date(self):
        w = withdrawn_list({"version": "1.1.0", "withdrawn_at": "2026-11-02", "reason": "Breaks.", "replacement": None})
        d = self.decide("1.0.0", "stable", candidate("1.1.0"), withdrawn_=w)
        self.assertEqual(d.action, "up_to_date")

    def test_a_withdrawn_installed_version_is_mentioned_but_never_downgraded(self):
        w = withdrawn_list({"version": "1.1.0", "withdrawn_at": "2026-11-02", "reason": "Breaks channels.",
                            "replacement": None})
        d = self.decide("1.1.0", "stable", candidate("1.0.0"), candidate("1.1.0"), withdrawn_=w)
        self.assertEqual(d.action, "up_to_date")
        self.assertIn("1.1.0 was withdrawn: Breaks channels.", d.message)
        self.assertEqual(str(d.installed_withdrawn.version), "1.1.0")
        d = self.decide("1.1.0", "stable", candidate("1.1.0"), candidate("1.1.1"), withdrawn_=w)
        self.assertEqual(str(d.target.version), "1.1.1", "a newer fix is offered")

    def test_an_invalid_release_blocks_without_falling_back(self):
        bad = select.Candidate(semver.parse("1.2.0"), problem="the manifest names another image")
        d = self.decide("1.0.0", "stable", candidate("1.1.0"), bad)
        self.assertEqual((d.action, d.reason), ("blocked", "invalid_release"))
        self.assertIn("1.2.0", d.message)
        bad_middle = select.Candidate(semver.parse("1.1.0"), problem="x")
        self.assertEqual(self.decide("1.0.0", "stable", bad_middle, candidate("1.2.0")).reason, "invalid_release")

    def test_invalid_releases_that_cant_be_chosen_dont_matter(self):
        old_bad = select.Candidate(semver.parse("0.9.0"), problem="x")
        withdrawn_bad = select.Candidate(semver.parse("1.2.0"), problem="x")
        rc_bad = select.Candidate(semver.parse("1.3.0-rc.1"), problem="x")
        w = withdrawn_list({"version": "1.2.0", "withdrawn_at": "2026-11-02", "reason": "Bad.", "replacement": None})
        d = self.decide("1.0.0", "stable", old_bad, candidate("1.1.0"), withdrawn_bad, rc_bad, withdrawn_=w)
        self.assertEqual(str(d.target.version), "1.1.0", "withdrawing a broken release unblocks updates")

    def test_duplicate_versions_block(self):
        self.assertEqual(self.decide("1.0.0", "stable", candidate("1.1.0"), candidate("1.1.0")).reason,
                         "duplicate_release")

    def test_stale_withdrawal_list_blocks(self):
        w = withdrawn_list(revision=3)
        self.assertEqual(self.decide("1.0.0", "stable", candidate("1.1.0"), withdrawn_=w, seen_revision=4).reason,
                         "withdrawn_stale")
        for seen in (None, 2, 3):
            with self.subTest(seen=seen):
                self.assertEqual(self.decide("1.0.0", "stable", candidate("1.1.0"), withdrawn_=w,
                                             seen_revision=seen).action, "update")

    def test_the_updater_must_be_new_enough(self):
        d = self.decide("1.0.0", "stable", candidate("1.1.0", **{"compatibility.min_updater": UPDATER_PROTOCOL + 1}))
        self.assertEqual(d.reason, "updater_too_old")
        d = self.decide("1.0.0", "stable", candidate("1.1.0", **{"compatibility.min_updater": 2}), updater_protocol=2)
        self.assertEqual(d.action, "update")

    def test_min_from_version_goes_through_an_intermediate_release(self):
        rels = (candidate("1.1.0", **{"compatibility.min_from_version": "1.0.0"}),
                candidate("1.2.0", **{"compatibility.min_from_version": "1.1.0"}))
        d = self.decide("1.0.0", "stable", *rels)
        self.assertEqual([str(m.version) for m in d.steps], ["1.1.0", "1.2.0"])
        self.assertIn("Update path: 1.1.0 then 1.2.0", d.message)
        d = self.decide("1.1.0", "stable", *rels)
        self.assertEqual([str(m.version) for m in d.steps], ["1.2.0"])

    def test_stepping_stones_are_installed_on_the_way(self):
        rels = (candidate("1.1.0", **{"compatibility.min_from_version": "1.0.0"}),
                candidate("1.2.0", **{"compatibility.min_from_version": "1.0.0"}),
                candidate("1.3.0", **{"compatibility.min_from_version": "1.0.0",
                                      "compatibility.stepping_stones": ["1.1.0"]}))
        d = self.decide("1.0.0", "stable", *rels)
        self.assertEqual([str(m.version) for m in d.steps], ["1.1.0", "1.3.0"],
                         "the stone is never skipped, even though 1.2.0 is newer")
        d = self.decide("1.1.0", "stable", *rels)
        self.assertEqual([str(m.version) for m in d.steps], ["1.3.0"], "a stone already passed isn't repeated")

    def test_a_stone_reached_through_its_own_path(self):
        rels = (candidate("1.1.0", **{"compatibility.min_from_version": "1.0.0"}),
                candidate("1.2.0", **{"compatibility.min_from_version": "1.1.0"}),
                candidate("1.4.0", **{"compatibility.min_from_version": "1.2.0",
                                      "compatibility.stepping_stones": ["1.2.0"]}))
        d = self.decide("1.0.0", "stable", *rels)
        self.assertEqual([str(m.version) for m in d.steps], ["1.1.0", "1.2.0", "1.4.0"])

    def test_no_path_blocks(self):
        rels = (candidate("1.2.0", **{"compatibility.min_from_version": "1.1.0"}),)
        d = self.decide("1.0.0", "stable", *rels)
        self.assertEqual((d.action, d.reason), ("blocked", "no_path"))
        w = withdrawn_list({"version": "1.1.0", "withdrawn_at": "2026-11-02", "reason": "Bad.", "replacement": "1.2.0"})
        rels = (candidate("1.1.0"), candidate("1.2.0", **{"compatibility.min_from_version": "1.1.0"}))
        self.assertEqual(self.decide("1.0.0", "stable", *rels, withdrawn_=w).reason, "no_path",
                         "a withdrawn intermediate release isn't used")
        stone_missing = (candidate("1.3.0", **{"compatibility.min_from_version": "1.0.0",
                                               "compatibility.stepping_stones": ["1.1.0"]}),)
        self.assertEqual(self.decide("1.0.0", "stable", *stone_missing).reason, "no_path")

    def test_a_stable_install_never_goes_through_a_prerelease(self):
        rels = (candidate("1.1.0-rc.1", **{"compatibility.min_from_version": "1.0.0"}),
                candidate("1.1.0", **{"compatibility.min_from_version": "1.1.0-rc.1"}))
        self.assertEqual(self.decide("1.0.0", "stable", *rels).reason, "no_path")
        d = self.decide("1.0.0", "prerelease", *rels)
        self.assertEqual([str(m.version) for m in d.steps], ["1.1.0-rc.1", "1.1.0"])


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "release_meta", *args], cwd=ROOT, capture_output=True,
                              text=True, timeout=60)

    def test_validate_the_withdrawn_file(self):
        res = self.run_cli("validate-withdrawn", "release/withdrawn.json")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("valid: revision", res.stdout)

    def test_validate_a_manifest(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            good, bad = Path(tmp) / "good.json", Path(tmp) / "bad.json"
            good.write_text(json.dumps(make_manifest("1.2.0")), encoding="utf-8")
            broken = make_manifest("1.2.0")
            broken["image"]["digest"] = "latest"
            bad.write_text(json.dumps(broken), encoding="utf-8")
            res = self.run_cli("validate-manifest", str(good))
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("@sha256:", res.stdout)
            res = self.run_cli("validate-manifest", str(bad))
            self.assertEqual(res.returncode, 1)
            self.assertIn("image.digest is malformed", res.stderr)
            res = self.run_cli("validate-manifest", str(Path(tmp) / "missing.json"))
            self.assertEqual(res.returncode, 1)


if __name__ == "__main__":
    unittest.main()
