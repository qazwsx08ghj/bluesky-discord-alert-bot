"""Command line (standard library only; no Django needed):

    python -m release_meta validate-manifest alertbot-release.json
    python -m release_meta validate-withdrawn release/withdrawn.json
    python -m release_meta check --installed 1.0.0-rc.2 --channel prerelease    # reads GitHub; changes nothing

Exit code 0: valid (or, for check, an update is available or it is up to date); 1: invalid or
blocked, with the reason on stderr.

"valid" means the file's format and internal consistency only (integrity). It says nothing about
who published it (authenticity): that comes from reading it from an immutable GitHub release of
this repository, which the check command does. See docs/RELEASES.md, section 1.
"""
import argparse
import json
import sys
from pathlib import Path

from . import MetadataError, github, manifest, withdrawn


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise MetadataError(f"{path} can't be read as JSON ({type(exc).__name__})") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m release_meta", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate-manifest").add_argument("file")
    sub.add_parser("validate-withdrawn").add_argument("file")
    check = sub.add_parser("check")
    check.add_argument("--installed", required=True)
    check.add_argument("--channel", choices=["stable", "prerelease"], required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-manifest":
            m = manifest.validate(_load(args.file))
            print(f"valid format: {m.version} ({m.channel}), image {m.image_reference}")
            print("(This checks the file only, not who published it: see docs/RELEASES.md, section 1.)")
        elif args.command == "validate-withdrawn":
            w = withdrawn.validate(_load(args.file))
            print(f"valid format: revision {w.revision}, {len(w.entries)} withdrawn version(s), content {w.digest[:16]}")
        else:
            decision = github.check(args.installed, args.channel)
            print(decision.message)
            return 0 if decision.action in ("update", "up_to_date") else 1
    except MetadataError as exc:
        print(f"invalid: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
