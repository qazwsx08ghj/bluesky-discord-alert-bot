"""Before a release is published: the tag, frontend/package.json and CHANGELOG.md must agree.

Run by .github/workflows/release.yml before anything is built or pushed:

    python docker/release_check.py v1.2.0 [--notes release-notes.md]

- v1.2.0 is a release: package.json says 1.2.0, and CHANGELOG.md has "## [1.2.0] - YYYY-MM-DD"
  with a real date and real repository links (no OWNER/REPOSITORY placeholders).
- v1.2.0-rc.1 (any suffix) is a pre-release, for testing: the same version in package.json and a
  CHANGELOG.md entry, which may still say "Unreleased". Pre-releases never become :stable, the
  image that users' update.bat downloads.

Prints version=…, prerelease=true|false and channel=… for $GITHUB_OUTPUT. --notes writes that
version's CHANGELOG.md section, for the GitHub release page. Standard library only.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAG = re.compile(r"^v(\d+\.\d+\.\d+)(-[0-9A-Za-z][0-9A-Za-z.-]*)?$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ReleaseError(Exception):
    pass


def changelog_section(changelog: str, version: str):
    """(date text, section body) of `## [version] - date`, or None."""
    match = re.search(rf"^## \[{re.escape(version)}\] - (.+)$", changelog, re.MULTILINE)
    if not match:
        return None
    rest = changelog[match.end():]
    end = re.search(r"^## \[|^\[[^\]]+\]: ", rest, re.MULTILINE)
    return match.group(1).strip(), rest[:end.start() if end else len(rest)].strip()


def check(tag: str, root: Path = ROOT) -> dict:
    match = TAG.match(tag)
    if not match:
        raise ReleaseError(f"{tag!r} isn't a version tag such as v1.2.0 or v1.2.0-rc.1")
    version, suffix = match.group(1), match.group(2)
    package = json.loads((root / "frontend" / "package.json").read_text(encoding="utf-8"))["version"]
    if package != version:
        raise ReleaseError(f"the tag is {version}, but frontend/package.json says {package}: update it first")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    section = changelog_section(changelog, version)
    if section is None:
        raise ReleaseError(f"CHANGELOG.md has no '## [{version}] - …' entry")
    date, body = section
    if not body:
        raise ReleaseError(f"CHANGELOG.md's entry for {version} is empty")
    if suffix is None:
        if not DATE.match(date):
            raise ReleaseError(f"CHANGELOG.md's entry for {version} needs its release date (YYYY-MM-DD), "
                               f"not {date!r}")
        if "OWNER/REPOSITORY" in changelog:
            raise ReleaseError("CHANGELOG.md still has OWNER/REPOSITORY placeholders in its links")
    return {"version": tag[1:], "prerelease": "true" if suffix else "false",
            "channel": "prerelease" if suffix else "stable", "notes": body}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tag")
    parser.add_argument("--notes", type=Path, help="write this version's changelog section here")
    args = parser.parse_args(argv)
    try:
        result = check(args.tag)
    except ReleaseError as exc:
        print(f"Release check failed: {exc}", file=sys.stderr)
        return 1
    if args.notes:
        args.notes.write_text(result["notes"] + "\n", encoding="utf-8", newline="\n")
    for key in ("version", "prerelease", "channel"):
        print(f"{key}={result[key]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
