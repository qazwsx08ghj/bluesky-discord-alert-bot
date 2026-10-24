"""Is there a newer release this install can update to? Run by update.bat in the installed image.

    docker run --rm <installed image> update-plan --channel prerelease [--seen-revision N --seen-digest D]

Reads GitHub's releases and release/withdrawn.json (release_meta.github.check: never older, fails
closed). Changes nothing. Prints KEY=value lines for update.bat (always exit code 0):

    RESULT=update|up_to_date|blocked
    REASON=...                     a short code
    CURRENT_VERSION=1.0.0-rc.3     this image's version
    MESSAGE=...                    one line, for the user
    TARGET_VERSION, TARGET_IMAGE   (update) the next step, pinned by digest: <image>:<version>@sha256:...
    FINAL_VERSION, STEPS           (update) where the path ends, and how many steps it has
    SEEN_REVISION, SEEN_DIGEST     the withdrawal list to remember once the update (or check) is done

update.bat installs one step per run. In an image built for the tests only, ALERTBOT_UPDATE_TEST_API
points it at a fake GitHub.
"""
import os
import re

from django.conf import settings
from django.core.management.base import BaseCommand

from release_meta import IMAGE, MetadataError, github, is_test_build, withdrawn

UNSAFE = re.compile(r"[&|<>^%!\"\r\n]")


def one_line(text: str) -> str:
    """Safe to print into a batch file's variable: no shell metacharacters, one line, short."""
    return UNSAFE.sub(" ", str(text))[:400]


class Command(BaseCommand):
    help = "Check for a newer release (read-only). Prints KEY=value lines for update.bat."

    def add_arguments(self, parser):
        parser.add_argument("--channel", required=True)
        parser.add_argument("--seen-revision", type=int)
        parser.add_argument("--seen-digest")

    def emit(self, **values):
        for key, value in values.items():
            self.stdout.write(f"{key}={one_line(value)}")

    def handle(self, *args, channel, seen_revision=None, seen_digest=None, **options):
        installed = settings.APP_VERSION
        test_api = os.getenv("ALERTBOT_UPDATE_TEST_API", "")
        try:
            client = github.Client(api=test_api, test_hosts=(test_api.rstrip("/") + "/",)) \
                if test_api and is_test_build() else github.Client()
            seen = None
            if seen_revision is not None or seen_digest:
                seen = withdrawn.Seen.from_json({"revision": seen_revision, "digest": seen_digest})
        except (MetadataError, ValueError) as exc:
            self.emit(RESULT="blocked", REASON="setup", CURRENT_VERSION=installed,
                      MESSAGE=f"Couldn't check for updates: {exc}. Nothing was changed.")
            return
        decision = github.check(installed, channel, client=client, seen=seen)
        values = {"RESULT": decision.action, "REASON": decision.reason, "CURRENT_VERSION": installed,
                  "MESSAGE": decision.message}
        if decision.action == "update":
            step = decision.steps[0]
            values.update(TARGET_VERSION=str(step.version), TARGET_IMAGE=f"{IMAGE}:{step.version}@{step.image_digest}",
                          FINAL_VERSION=str(decision.target.version), STEPS=str(len(decision.steps)))
        if decision.remember is not None:
            values.update(SEEN_REVISION=str(decision.remember.revision), SEEN_DIGEST=decision.remember.digest)
        self.emit(**values)
