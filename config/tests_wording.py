"""User-facing wording: the bot sends Discord *notifications* (not "announcements"), and nothing a user
or API client reads mentions the project's internal development steps.

API field names such as `announce_posts` and `new_posts_announced` are part of the API and stay as
they are. "#announcements" as an example Discord channel name is fine: it's a channel's name.
"""
import ast
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)
ANNOUNCE = re.compile(r"\bannounc(e|ed|es|ing|ement)\b", re.IGNORECASE)
DEV_STEP = re.compile(r"\b(since|until|before|in|from) step \d+\b|\(step \d+\)|^step \d+:", re.IGNORECASE | re.MULTILINE)
PANEL_IDENTIFIERS = re.compile(r"\bannounce_(posts|reposts|replies)\b|\bsetAnnounce\b|\bannounce\.\w+|\bannounce\b(?=\s*[,}\]=)])")


def python_strings(path):
    """Every string literal in a Python file (messages, docstrings)."""
    return [node.value for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)]


class PanelWordingTests(SimpleTestCase):
    def test_the_panel_talks_about_notifications(self):
        for path in (ROOT / "frontend" / "src").rglob("*.tsx"):
            if "test" in path.parts:
                continue
            text = PANEL_IDENTIFIERS.sub("", path.read_text(encoding="utf-8"))
            with self.subTest(file=path.name):
                self.assertIsNone(ANNOUNCE.search(text), "say 'notification' for what the bot sends to Discord")


class BackendMessageTests(SimpleTestCase):
    MESSAGE_MODULES = ["activity/problems.py", "activity/status.py", "bluesky_monitor/account_status.py",
                       "bluesky_monitor/check_all.py", "bluesky_monitor/poller.py", "bluesky_monitor/jobs.py",
                       "config/frontend_views.py", "discord_alerts/channels.py", "discord_alerts/sender.py"]

    def test_messages_talk_about_notifications(self):
        for name in self.MESSAGE_MODULES:
            for value in python_strings(ROOT / name):
                with self.subTest(file=name, text=value[:60]):
                    self.assertIsNone(ANNOUNCE.search(value))


class ApiDescriptionTests(SimpleTestCase):
    def test_the_api_contract_has_no_development_history(self):
        contract = (ROOT / "docs" / "openapi.yaml").read_text(encoding="utf-8")
        self.assertIsNone(DEV_STEP.search(contract))
        self.assertIsNone(ANNOUNCE.search(re.sub(r"\b(new_posts_announced|not_announced|announce_\w+)\b", "", contract)))

    def test_application_code_has_no_development_step_labels(self):
        """Comments and docstrings describe the code as it is; history is in docs/history/."""
        for path in ROOT.rglob("*.py"):
            parts = set(path.relative_to(ROOT).parts)
            if parts & {"venv", "node_modules", "migrations"} or path.name == "tests_wording.py":
                continue
            with self.subTest(file=str(path.relative_to(ROOT))):
                self.assertIsNone(DEV_STEP.search(path.read_text(encoding="utf-8")))
