"""Release documents: links, the two languages, the policy files, templates and the release check."""
import importlib.util
import json
import re
import shutil
import tempfile
from pathlib import Path

import yaml
from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)
PAIRS = [("README.md", "README.zh-TW.md"), ("CONTRIBUTING.md", "CONTRIBUTING.zh-TW.md")]
DOCS = [*ROOT.glob("*.md"), *(ROOT / "docs").glob("*.md"), *(ROOT / "docker" / "notices").glob("*.md"),
        ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"]
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")
FENCE = re.compile(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```", re.MULTILINE | re.DOTALL)
DOCKER_TERMS = "https://docs.docker.com/subscription/desktop-license/"


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "docker" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def text(name):
    return (ROOT / name).read_text(encoding="utf-8")


def links(markdown):
    return [m.group(1) for m in LINK.finditer(FENCE.sub("", markdown))]


def headings(markdown):
    return [line.split(" ", 1)[0] for line in FENCE.sub("", markdown).splitlines() if re.match(r"#{1,4} ", line)]


def code_blocks(markdown):
    return [re.sub(r"(?m)^[ \t]+", "", block) for block in FENCE.findall(markdown)]


class LinkTests(SimpleTestCase):
    def test_every_relative_link_and_image_exists(self):
        for doc in DOCS:
            for target in links(doc.read_text(encoding="utf-8")):
                if re.match(r"^(https?:|mailto:|#)", target):
                    continue
                path = (doc.parent / target.split("#", 1)[0]).resolve()
                with self.subTest(doc=doc.name, link=target):
                    self.assertTrue(path.exists(), f"{doc.name}: {target}")


class TranslationTests(SimpleTestCase):
    """The Traditional Chinese documents follow their English originals."""

    def test_each_pair_links_to_the_other_at_the_top(self):
        for en, zh in PAIRS:
            self.assertIn(f"**English** | [繁體中文]({zh})", text(en)[:300])
            self.assertIn(f"[English]({en}) | **繁體中文**", text(zh)[:300])

    def test_same_sections_in_the_same_order(self):
        for en, zh in PAIRS:
            with self.subTest(doc=en):
                self.assertEqual(headings(text(en)), headings(text(zh)))

    def test_commands_and_code_are_identical(self):
        for en, zh in PAIRS:
            with self.subTest(doc=en):
                self.assertEqual(code_blocks(text(en)), code_blocks(text(zh)))

    def test_every_english_link_is_also_in_the_translation(self):
        for en, zh in PAIRS:
            translated = set(links(text(zh)))
            for target in links(text(en)):
                if target.startswith("#") or target in (en, zh):
                    continue
                localized = target.replace(".md", ".zh-TW.md") if ".md" in target else target
                with self.subTest(doc=en, link=target):
                    self.assertTrue(target in translated or localized in translated, f"{zh} lacks {target}")

    def test_glossary_exists_and_is_linked(self):
        self.assertIn("docs/GLOSSARY.zh-TW.md", text("CONTRIBUTING.md"))
        self.assertIn("| App Password | 應用程式密碼 |", text("docs/GLOSSARY.zh-TW.md"))


class DockerDesktopNoticeTests(SimpleTestCase):
    def test_users_learn_about_docker_desktops_license_everywhere(self):
        bundle = load("make_release_bundle")
        for name, content, sentence in [
            ("README.md", text("README.md"), "check that you qualify for free use"),
            ("README.zh-TW.md", text("README.zh-TW.md"), "請先確認是否符合免費使用的資格"),
            ("README.txt", bundle.README, "check that you qualify"),
            ("README.zh-TW.txt", bundle.README_ZH_TW, "請先確認是否符合免費使用的資格"),
        ]:
            with self.subTest(doc=name):
                self.assertIn(DOCKER_TERMS, content)
                self.assertIn(sentence, content)

    def test_downloaded_scripts_warning_is_explained(self):
        self.assertIn("Unblock", text("README.md"))
        self.assertIn("解除封鎖", text("README.zh-TW.md"))
        self.assertIn("Run anyway", load("make_release_bundle").README)


class PolicyFileTests(SimpleTestCase):
    def test_security_policy(self):
        policy = text("SECURITY.md")
        for required in ("Latest release", "Report a vulnerability", "within 7 days", "Never include real secrets",
                         "## 繁體中文摘要", "127.0.0.1"):
            self.assertIn(required, policy)

    def test_ai_assisted_development_is_disclosed(self):
        en, zh = text("CONTRIBUTING.md"), text("CONTRIBUTING.zh-TW.md")
        self.assertIn("## 10. How this project is developed", en)
        self.assertIn("human-directed, AI-assisted agentic development", en)
        self.assertIn("Not every line of AI-written code has been read by a human", en)
        self.assertIn("This is not a reason to reject a contribution", en)
        self.assertIn("## 10. 這個專案的開發方式", zh)
        self.assertIn("並沒有每一行都經過人工逐行審閱", zh)
        self.assertIn("## AI assistance / AI 協助", text(".github/PULL_REQUEST_TEMPLATE.md"))
        self.assertNotIn("AI", text("README.md").split("## For developers")[0], "the user guide stays about using the bot")

    def test_testing_reference_is_linked_and_honest_about_limits(self):
        testing = text("docs/TESTING.md")
        for linked_from in ("CONTRIBUTING.md", "CONTRIBUTING.zh-TW.md", "docs/BACKEND.md", "docs/FRONTEND.md",
                            "docs/DOCKER.md", "README.md", "README.zh-TW.md"):
            self.assertIn("TESTING.md", text(linked_from), linked_from)
        self.assertIn("**Deferred.**", testing)
        self.assertIn("under emulation only", testing)
        self.assertIn("Not tested on real ARM hardware", testing)
        self.assertIn("Not every line of AI-written code has been read by a human", text("CONTRIBUTING.md"))
        for command in ("python manage.py test --tag contract", "python docker/third_party_notices.py",
                        "npm run e2e", "set RUN_DOCKER_TESTS=1"):
            self.assertIn(command, testing)
        ci = text(".github/workflows/ci.yml")
        for job in ("backend:", "contract:", "frontend:", "browser:", "windows-scripts:", "docker:"):
            self.assertIn(f"\n  {job}\n", ci, "TESTING.md describes exactly these six CI jobs")

    def test_changelog_matches_the_panel_version(self):
        changelog = text("CHANGELOG.md")
        self.assertIn("## [Unreleased]", changelog)
        versions = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE)
        package = json.loads(text("frontend/package.json"))["version"]
        self.assertEqual(versions[0], package, "the newest CHANGELOG.md entry is the version in package.json")

    def test_templates_are_valid_and_warn_about_secrets(self):
        folder = ROOT / ".github" / "ISSUE_TEMPLATE"
        forms = {p.name: yaml.safe_load(p.read_text(encoding="utf-8")) for p in folder.glob("*.yml")}
        self.assertEqual(set(forms), {"bug_report.yml", "feature_request.yml", "config.yml"})
        self.assertFalse(forms["config.yml"]["blank_issues_enabled"])
        for name in ("bug_report.yml", "feature_request.yml"):
            form = forms[name]
            self.assertTrue(form["name"] and form["description"] and form["body"], name)
            ids = [item["id"] for item in form["body"] if "id" in item]
            self.assertEqual(len(ids), len(set(ids)), name)
        bug = forms["bug_report.yml"]
        self.assertIn("Never paste secrets", bug["body"][0]["attributes"]["value"])
        secrets = [item for item in bug["body"] if item.get("id") == "secrets"][0]
        self.assertTrue(secrets["attributes"]["options"][0]["required"])
        pr = text(".github/PULL_REQUEST_TEMPLATE.md")
        self.assertIn("No secrets or real data", pr)
        self.assertIn("third_party_notices.py --write", pr)


class ReleaseCheckTests(SimpleTestCase):
    """docker/release_check.py, run by release.yml before anything is published."""

    def setUp(self):
        self.rc = load("release_check")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "frontend").mkdir()
        shutil.copy(ROOT / "frontend" / "package.json", self.root / "frontend" / "package.json")
        self.version = json.loads(text("frontend/package.json"))["version"]
        self.changelog = text("CHANGELOG.md")
        self.write(self.changelog)

    def write(self, changelog):
        (self.root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")

    def check(self, tag):
        return self.rc.check(tag, self.root)

    def fails(self, tag, message):
        with self.assertRaises(self.rc.ReleaseError) as ctx:
            self.check(tag)
        self.assertIn(message, str(ctx.exception))

    def test_a_prerelease_needs_only_matching_versions(self):
        result = self.check(f"v{self.version}-rc.1")
        self.assertEqual((result["prerelease"], result["channel"]), ("true", "prerelease"))
        self.assertIn("### Added", result["notes"])
        self.assertNotIn("[Unreleased]:", result["notes"])

    def test_a_release_needs_a_date_and_real_links(self):
        self.fails(f"v{self.version}", "needs its release date")
        dated = self.changelog.replace(f"## [{self.version}] - Unreleased", f"## [{self.version}] - 2026-11-01")
        self.write(dated + "\n[0.0.1]: https://github.com/OWNER/REPOSITORY/releases/tag/v0.0.1\n")
        self.fails(f"v{self.version}", "OWNER/REPOSITORY")
        self.write(dated)
        result = self.check(f"v{self.version}")
        self.assertEqual((result["prerelease"], result["version"]), ("false", self.version))

    def test_mismatches_and_bad_tags_are_refused(self):
        self.fails("v9.9.9", "frontend/package.json says")
        self.fails("1.0.0", "isn't a version tag")
        self.fails("v1.0", "isn't a version tag")
        self.write(self.changelog.replace(f"## [{self.version}]", "## [0.0.1]"))
        self.fails(f"v{self.version}-rc.1", "no '## [")

    def test_the_current_tree_is_ready_for_a_prerelease_only(self):
        self.assertEqual(self.rc.main([f"v{self.version}-rc.1"]), 0)
        self.assertEqual(self.rc.main([f"v{self.version}"]), 1, "1.0.0 isn't dated yet")

    def test_the_workflow_never_marks_a_prerelease_stable(self):
        workflow = yaml.safe_load(text(".github/workflows/release.yml"))
        jobs = workflow["jobs"]
        self.assertEqual(jobs["publish"]["needs"], ["version", "checks"])
        self.assertIn("docker/release_check.py", jobs["version"]["steps"][1]["run"])
        names = [s for s in jobs["publish"]["steps"] if s.get("id") == "names"][0]["run"]
        stable_branch, prerelease_branch = names.split("else", 1)
        self.assertIn('if [ "$PRERELEASE" = "false" ]', stable_branch)
        self.assertIn(":stable", stable_branch)
        self.assertNotIn("stable", prerelease_branch)
        release = [s for s in jobs["publish"]["steps"] if "action-gh-release" in s.get("uses", "")][0]["with"]
        self.assertIn("prerelease", release)
        self.assertEqual(release["body_path"], "release-notes.md")
