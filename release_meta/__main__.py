"""Command line (standard library only; no Django needed):

    python -m release_meta validate-manifest alertbot-release.json
    python -m release_meta validate-withdrawn release/withdrawn.json
    python -m release_meta check --installed 1.0.0-rc.2 --channel prerelease    # reads GitHub; changes nothing
    python -m release_meta generate --version 1.2.0 --commit <sha> --run-id <id> --index-digest sha256:<...> \
        --index index.json --describe describe.json --bundle dist/BlueskyDiscordAlertBot-1.2.0.zip \
        [--linux-bundle dist/BlueskyDiscordAlertBot-1.2.0-linux.tar.gz] \
        --compatibility release/compatibility.json --out dist        # writes alertbot-release.json, SHA256SUMS
    python -m release_meta verify-checksums dist

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

from . import MetadataError, generate, github, manifest, withdrawn


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
    gen = sub.add_parser("generate")
    for option in ("--version", "--commit", "--run-id", "--index-digest", "--index", "--describe", "--bundle",
                   "--compatibility", "--out"):
        gen.add_argument(option, required=True)
    gen.add_argument("--linux-bundle", help="the Linux download (.tar.gz); needed when the image has Linux files")
    sub.add_parser("verify-checksums").add_argument("folder")
    sub.add_parser("validate-compatibility").add_argument("file")
    sub.add_parser("check-withdrawn-history").add_argument("file", help="release/withdrawn.json, in a full git clone")
    verify = sub.add_parser("verify-release")
    verify.add_argument("tag")
    verify.add_argument("--expect", help="the manifest that was built; the published one must be identical")
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-compatibility":
            data = generate.read_compatibility(_load(args.file))
            print(f"valid format: updates from {data['min_from_version']}, updater protocol {data['min_updater']}+")
            return 0
        if args.command == "check-withdrawn-history":
            revisions = generate.check_withdrawn_history(Path(args.file).as_posix(), Path.cwd())
            print(f"revisions in order: {revisions}")
            return 0
        if args.command == "verify-release":
            expected = Path(args.expect).read_bytes() if args.expect else None
            m = github.verify_release(github.Client(), args.tag, expected)
            print(f"verified: {args.tag} is immutable, its manifest is valid and matches it; image {m.image_reference}")
            return 0
        if args.command == "generate":
            try:
                index_raw = Path(args.index).read_bytes()
            except OSError:
                raise MetadataError(f"{args.index} can't be read") from None
            data = generate.generate(version=args.version, commit=args.commit, run_id=args.run_id,
                                     index_digest=args.index_digest, index_raw=index_raw,
                                     describe=_load(args.describe), bundle_path=Path(args.bundle),
                                     compatibility=_load(args.compatibility),
                                     linux_bundle_path=Path(args.linux_bundle) if args.linux_bundle else None)
            linux = Path(args.linux_bundle) if args.linux_bundle else None
            for path in generate.write_release_files(Path(args.out), data, Path(args.bundle), linux):
                print(f"written: {path}")
            return 0
        if args.command == "verify-checksums":
            names = generate.verify_checksums(Path(args.folder))
            print(f"checksums match: {', '.join(names)}")
            return 0
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
