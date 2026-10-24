"""The release workflow (.github/workflows/release.yml): checked as a file, and its shell steps run.

What must hold (docs/RELEASES.md, section 5):
- started by hand only; pushing a tag starts nothing;
- nothing has write access before the maintainer approves the `release` environment;
- after approval, the visible actions happen in a fixed order: push by digest, attestations, tag,
  draft, publish, then the image tags, and `:stable` last, for final releases only;
- the download is built from the image's own files and pinned by digest; the manifest comes from
  `release_meta generate`, and the published release is read back like an updater does;
- every action is pinned to a full commit SHA, in this workflow and in ci.yml (which it runs);
- the version typed in never reaches a shell directly (only through environment variables).
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml
from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)
WORKFLOWS = ROOT / ".github" / "workflows"
SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}$")


def load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def triggers(workflow):
    return workflow.get("on", workflow.get(True))  # YAML 1.1 reads a bare `on` as true


class ReleaseWorkflowTests(SimpleTestCase):
    def setUp(self):
        self.wf = load("release.yml")
        self.jobs = self.wf["jobs"]
        self.publish = self.jobs["publish"]["steps"]

    def step(self, job, name=None, step_id=None, uses=None):
        for s in self.jobs[job]["steps"]:
            if (step_id and s.get("id") == step_id) or (name and s.get("name") == name) or \
                    (uses and uses in s.get("uses", "")):
                return s
        self.fail(f"{job}: no step {name or step_id or uses}")

    def index(self, predicate):
        return next(i for i, s in enumerate(self.publish) if predicate(s))

    # -- how it starts, and who may do what ----------------------------------------------------------
    def test_started_by_hand_only(self):
        on = triggers(self.wf)
        self.assertEqual(set(on), {"workflow_dispatch"}, "no push, tag or schedule trigger")
        version = on["workflow_dispatch"]["inputs"]["version"]
        self.assertTrue(version["required"])

    def test_no_write_access_before_approval(self):
        self.assertEqual(self.wf["permissions"], {}, "nothing by default")
        self.assertEqual(self.jobs["prepare"]["permissions"], {"contents": "read"})
        self.assertEqual(self.jobs["checks"]["permissions"], {"contents": "read"})
        self.assertNotIn("environment", self.jobs["prepare"])
        self.assertEqual(load("ci.yml")["permissions"], {"contents": "read"})

    def test_publishing_needs_the_approved_environment(self):
        job = self.jobs["publish"]
        self.assertEqual(job["environment"], "release")
        self.assertEqual(sorted(job["needs"]), ["checks", "prepare"])
        self.assertEqual(job["permissions"], {"contents": "write", "packages": "write", "id-token": "write",
                                              "attestations": "write"})
        self.assertEqual(self.jobs["checks"]["uses"], "./.github/workflows/ci.yml")

    def test_one_release_at_a_time(self):
        self.assertEqual(self.wf["concurrency"], {"group": "release", "cancel-in-progress": False})

    def test_only_from_main_and_only_new_versions(self):
        guard = self.step("prepare", name="Only from main")
        self.assertEqual(guard["if"], "github.ref != 'refs/heads/main'")
        self.assertIn("exit 1", guard["run"])
        new = self.step("prepare", name="Not released yet")["run"]
        self.assertIn('git ls-remote --exit-code --tags origin "refs/tags/v$VERSION"', new)
        self.assertIn('gh release view "v$VERSION"', new)
        meta = self.step("prepare", name="The release metadata files are valid")["run"]
        for command in ("validate-withdrawn", "check-withdrawn-history", "validate-compatibility"):
            self.assertIn(f"python -m release_meta {command}", meta)
        self.assertEqual(self.jobs["prepare"]["steps"][0]["with"]["fetch-depth"], 0, "the history check needs it")

    def test_the_approved_commit_is_the_one_built(self):
        checkout = self.publish[0]
        self.assertTrue(checkout["uses"].startswith("actions/checkout@"))
        self.assertEqual(checkout["with"]["ref"], "${{ needs.prepare.outputs.commit }}")
        self.assertEqual(self.jobs["prepare"]["outputs"]["commit"], "${{ github.sha }}")

    # -- what is published, in what order --------------------------------------------------------------
    def test_built_and_pushed_by_digest_only(self):
        build = self.step("publish", step_id="build")["with"]
        self.assertNotIn("tags", build, "no tag at build time")
        self.assertIn("push-by-digest=true", build["outputs"])
        self.assertEqual(build["platforms"], "linux/amd64,linux/arm64")
        self.assertEqual(build["build-args"], "ALERTBOT_VERSION=${{ needs.prepare.outputs.version }}")
        for key in ("org.opencontainers.image.source=", "org.opencontainers.image.licenses=MIT"):
            self.assertIn(key, build["labels"])

    def test_read_back_by_digest_and_built_from_the_images_files(self):
        image = self.step("publish", step_id="image")["run"]
        for part in ('imagetools inspect --raw "$IMAGE@$DIGEST"', '"$IMAGE@$DIGEST" describe',
                     "docker cp bundle:/app/bundle/windows build/", "^sha256:[0-9a-f]{64}$"):
            self.assertIn(part, image)
        files = self.step("publish", name="The download, the manifest and SHA256SUMS")["run"]
        self.assertIn('--image "$IMAGE:$VERSION@$DIGEST" --from-bundle build/windows', files)
        self.assertIn("python -m release_meta generate", files)
        self.assertIn("python -m release_meta verify-checksums dist", files)

    def test_the_visible_actions_happen_in_order(self):
        order = [
            self.index(lambda s: s.get("id") == "build"),
            self.index(lambda s: "attest-build-provenance" in s.get("uses", "")),
            self.index(lambda s: s.get("name") == "Tag the approved commit"),
            self.index(lambda s: "--draft --verify-tag" in s.get("run", "")),
            self.index(lambda s: s.get("name") == "Check the draft's files"),
            self.index(lambda s: "--draft=false" in s.get("run", "")),
            self.index(lambda s: "verify-release" in s.get("run", "")),
            self.index(lambda s: '--tag "$IMAGE:$VERSION"' in s.get("run", "")),
            self.index(lambda s: '--tag "$IMAGE:stable"' in s.get("run", "")),
        ]
        self.assertEqual(order, sorted(order))
        self.assertEqual(order[-1], len(self.publish) - 1, ":stable is the very last step")

    def test_stable_moves_only_for_final_releases_and_only_by_digest(self):
        stable = [s for s in self.publish if ":stable" in s.get("run", "")]
        self.assertEqual(len(stable), 1)
        self.assertEqual(stable[0]["if"], "needs.prepare.outputs.prerelease == 'false'")
        self.assertIn('"$IMAGE@$DIGEST"', stable[0]["run"])
        text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        self.assertEqual(text.count("stable\""), 1, "no other step names :stable")

    def test_the_tag_is_created_at_the_approved_commit(self):
        run = self.step("publish", name="Tag the approved commit")["run"]
        self.assertIn('-f ref="refs/tags/v$VERSION" -f sha="$COMMIT"', run)
        self.assertEqual(self.jobs["publish"]["env"]["COMMIT"], "${{ needs.prepare.outputs.commit }}")

    def test_the_draft_is_checked_and_the_published_release_read_back(self):
        draft = self.step("publish", name="Check the draft's files")["run"]
        self.assertIn("verify-checksums build/check", draft)
        self.assertIn('cmp "$f"', draft)
        published = self.step("publish", name="Check the published release")["run"]
        self.assertEqual(published, 'python -m release_meta verify-release "v$VERSION" --expect dist/alertbot-release.json')

    # -- supply chain -----------------------------------------------------------------------------------
    def test_every_action_is_pinned_to_a_commit(self):
        for name in ("release.yml", "ci.yml"):
            text = (WORKFLOWS / name).read_text(encoding="utf-8")
            for uses in re.findall(r"uses:\s*(\S+)", text):
                if uses.startswith("./"):
                    continue
                with self.subTest(workflow=name, uses=uses):
                    self.assertRegex(uses, SHA_PIN)
            for line in text.splitlines():
                if "uses:" in line and "@" in line and not line.strip().startswith("#"):
                    self.assertRegex(line, r"@[0-9a-f]{40} # v\d+\.\d+\.\d+$", "the version is noted")

    def test_the_typed_version_never_reaches_a_shell_directly(self):
        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "run":
                        self.assertNotIn("${{", value, f"run steps use environment variables only: {value[:80]}")
                    else:
                        walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(self.wf)
        text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        self.assertEqual(text.count("${{ inputs.version }}"), 1, "only into the version check's environment")
        self.assertEqual(self.step("prepare", step_id="check")["env"]["VERSION"], "${{ inputs.version }}")

    def test_python_is_set_up_before_it_is_used(self):
        for job in ("prepare", "publish"):
            steps = self.jobs[job]["steps"]
            setup = next(i for i, s in enumerate(steps) if s.get("uses", "").startswith("actions/setup-python@"))
            first_python = next(i for i, s in enumerate(steps) if "python " in s.get("run", ""))
            self.assertLess(setup, first_python, job)
            self.assertEqual(steps[setup]["with"]["python-version"], "3.11")

    # -- the shell steps, run ---------------------------------------------------------------------------
    def bash(self):
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("needs bash")
        return bash

    def run_step(self, script, **env):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            # As GitHub runs a step: bash --noprofile --norc -eo pipefail
            res = subprocess.run([self.bash(), "--noprofile", "--norc", "-eo", "pipefail", "-c", script], cwd=ROOT,
                                 capture_output=True, text=True,
                                 env={**os.environ, "GITHUB_OUTPUT": str(out), **env}, timeout=60)
            outputs = dict(l.split("=", 1) for l in out.read_text(encoding="utf-8").splitlines()) if out.exists() else {}
            return res.returncode, outputs

    def test_the_image_name_must_be_the_trusted_one(self):
        script = self.step("publish", step_id="names")["run"]
        code, out = self.run_step(script, GITHUB_REPOSITORY="Qazwsx08ghj/Bluesky-Discord-Alert-Bot")
        self.assertEqual((code, out.get("image")), (0, "ghcr.io/qazwsx08ghj/bluesky-discord-alert-bot"))
        code, out = self.run_step(script, GITHUB_REPOSITORY="someone/fork-of-the-bot")
        self.assertNotEqual(code, 0, "a fork's workflow can't publish under another name")
        self.assertNotIn("image", out)

    def test_a_missing_digest_stops_before_anything_else(self):
        script = self.step("publish", step_id="image")["run"]
        code, _ = self.run_step(script, IMAGE="ghcr.io/x/y", DIGEST="")
        self.assertEqual(code, 1)
        code, _ = self.run_step(script, IMAGE="ghcr.io/x/y", DIGEST="sha256:abc")
        self.assertEqual(code, 1)
