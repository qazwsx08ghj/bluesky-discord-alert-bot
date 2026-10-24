"""Building a release's manifest and checksums from what was actually built. See docs/RELEASES.md.

    python -m release_meta generate --version 1.2.0 --commit <sha> --run-id <id> \\
        --index-digest sha256:<...> --index index.json --describe describe.json \\
        --bundle dist/BlueskyDiscordAlertBot-1.2.0.zip --linux-bundle dist/BlueskyDiscordAlertBot-1.2.0-linux.tar.gz \
        --compatibility release/compatibility.json --out dist
    python -m release_meta verify-checksums dist

Inputs, each checked before anything is written:
- the image index (`docker buildx imagetools inspect --raw <image>@<digest>`): its SHA-256 must be
  the digest given, so the platform list really belongs to that digest;
- what the image says about itself (`describe`, run by digest): its version must be the release's,
  its deployment files must be complete;
- the downloads: the Windows zip and the Linux .tar.gz, each exactly the image's deployment files
  for that platform with the same hashes, plus docker/compose.env (zip) or docker/release.env
  (tar.gz) pinning this image **by digest**;
- release/compatibility.json (min_updater, min_from_version, stepping_stones, compose_min).

The result must pass release_meta.manifest.validate, the same check updaters make. Output:
alertbot-release.json and SHA256SUMS (the downloads and the manifest), both byte-for-byte reproducible
from the same inputs.

What this proves is integrity (the manifest describes these exact bytes); who published it is a
separate question, answered by where it is published (docs/RELEASES.md, section 1).
"""
import gzip
import hashlib
import io
import json
import re
import tarfile
import zipfile
from pathlib import Path

from . import IMAGE, MANIFEST_ASSET, REPOSITORY, MetadataError, manifest, semver  # noqa: F401
from .bundle import NEVER_BUNDLED

COMPAT_SCHEMA = "alertbot-compatibility/1"
INDEX_TYPES = ("application/vnd.oci.image.index.v1+json",
               "application/vnd.docker.distribution.manifest.list.v2+json")
COMPOSE_ENV = "docker/compose.env"
RELEASE_ENV = "docker/release.env"
LINUX_FOLDER = "bluesky-alert-bot"
MAX_TAR_BYTES = 32 * 1024 * 1024
CHECKSUMS = "SHA256SUMS"
MAX_ZIP_ENTRIES = 200
MAX_ENTRY_BYTES = 8 * 1024 * 1024


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def platforms_from_index(raw: bytes, digest: str) -> dict:
    """{"linux/amd64": "sha256:...", ...} from the raw image index, whose SHA-256 must be digest."""
    if not manifest.DIGEST.fullmatch(digest or ""):
        raise MetadataError(f"the image digest is malformed: {str(digest)[:80]!r}")
    if f"sha256:{sha256(raw)}" != digest:
        raise MetadataError("the image index doesn't match the image digest")
    try:
        index = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise MetadataError("the image index isn't valid JSON") from None
    if not isinstance(index, dict) or index.get("mediaType") not in INDEX_TYPES:
        raise MetadataError("the image digest isn't a multi-platform image index")
    platforms = {}
    for entry in index.get("manifests") or []:
        plat = entry.get("platform") or {}
        os_, arch = plat.get("os"), plat.get("architecture")
        if os_ == "unknown" or arch == "unknown":
            continue  # build attestations, not a platform
        key = f"{os_}/{arch}" + (f"/{plat['variant']}" if plat.get("variant") else "")
        if key in platforms:
            raise MetadataError(f"the image index lists {key} twice")
        if not manifest.DIGEST.fullmatch(str(entry.get("digest"))):
            raise MetadataError(f"the image index has a malformed digest for {key}")
        platforms[key] = entry["digest"]
    if "linux/amd64" not in platforms:
        raise MetadataError("the image has no linux/amd64 version")
    return dict(sorted(platforms.items()))


def read_zip(path: Path) -> dict:
    """{name: bytes} of the download, with every entry checked to be a safe, ordinary file."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(Path(path).read_bytes()))
    except (OSError, zipfile.BadZipFile):
        raise MetadataError(f"{Path(path).name} isn't a readable zip") from None
    with zf:
        entries = zf.infolist()
        if len(entries) > MAX_ZIP_ENTRIES:
            raise MetadataError("the download has too many files")
        files = {}
        for e in entries:
            name = e.filename
            if e.is_dir():
                continue
            if name.startswith("/") or "\\" in name or ".." in name.split("/") or ":" in name.split("/")[0]:
                raise MetadataError(f"the download has an unsafe path {name[:80]!r}")
            if name in files:
                raise MetadataError(f"the download has {name} twice")
            if e.file_size > MAX_ENTRY_BYTES:
                raise MetadataError(f"{name} in the download is larger than allowed")
            files[name] = zf.read(e)
    return files


def _safe_name(name: str) -> bool:
    return bool(name) and not name.startswith("/") and "\\" not in name and ".." not in name.split("/") \
        and ":" not in name.split("/")[0]


def read_tar(path: Path) -> dict:
    """{name: bytes} of the Linux download, without its bluesky-alert-bot/ folder: only folders and
    ordinary files with safe paths (no links, devices or anything outside that folder)."""
    try:
        raw = gzip.decompress(Path(path).read_bytes())
        if len(raw) > MAX_TAR_BYTES:
            raise MetadataError(f"{Path(path).name} is larger than allowed")
        tar = tarfile.open(fileobj=io.BytesIO(raw), mode="r:")
    except (OSError, EOFError, tarfile.TarError):
        raise MetadataError(f"{Path(path).name} isn't a readable .tar.gz") from None
    files = {}
    with tar:
        entries = tar.getmembers()
        if len(entries) > MAX_ZIP_ENTRIES:
            raise MetadataError("the Linux download has too many files")
        for e in entries:
            if not _safe_name(e.name) or (e.name != LINUX_FOLDER and not e.name.startswith(LINUX_FOLDER + "/")):
                raise MetadataError(f"the Linux download has an unsafe path {e.name[:80]!r}")
            if e.isdir():
                continue
            if not e.isfile():
                raise MetadataError(f"the Linux download has a link or special file: {e.name[:80]!r}")
            name = e.name[len(LINUX_FOLDER) + 1:]
            if name in files:
                raise MetadataError(f"the Linux download has {name} twice")
            if e.size > MAX_ENTRY_BYTES:
                raise MetadataError(f"{name} in the Linux download is larger than allowed")
            files[name] = tar.extractfile(e).read()
    return files


def _check_download(files: dict, image_files: dict, pin_file: str, version: str, index_digest: str, what: str):
    """The download holds exactly the image's deployment files, plus pin_file pinning this image by digest."""
    if pin_file not in files:
        raise MetadataError(f"the {what} has no {pin_file}")
    pinned = re.findall(r"(?m)^ALERTBOT_IMAGE=(.*?)\r?$", files[pin_file].decode("utf-8", "replace"))
    allowed_pins = {f"{IMAGE}@{index_digest}", f"{IMAGE}:{version}@{index_digest}"}
    if len(pinned) != 1 or pinned[0] not in allowed_pins:
        raise MetadataError(f"the {what}'s {pin_file} must pin this image by its digest "
                            f"({IMAGE}:{version}@{index_digest})")
    others = {n: d for n, d in files.items() if n != pin_file}
    if set(others) != set(image_files):
        raise MetadataError(f"the {what}'s files differ from the image's: missing {sorted(set(image_files) - set(others))}, "
                            f"unexpected {sorted(set(others) - set(image_files))}"[:300])
    for name, data in others.items():
        if sha256(data) != image_files[name]:
            raise MetadataError(f"{name} in the {what} differs from the image's")
    leaked = [n for n in files if n != pin_file and (n in NEVER_BUNDLED or n.endswith(".env"))]
    if leaked:
        raise MetadataError(f"the {what} must not contain {leaked}")


def read_compatibility(data) -> dict:
    if not isinstance(data, dict) or data.get("schema") != COMPAT_SCHEMA:
        raise MetadataError("release/compatibility.json has an unknown schema")
    allowed = {"schema", "min_updater", "min_from_version", "stepping_stones", "compose_min"}
    unknown = set(data) - allowed
    if unknown:
        raise MetadataError(f"release/compatibility.json has unknown fields: {sorted(unknown)}")
    missing = sorted(allowed - set(data))
    if missing:
        raise MetadataError(f"release/compatibility.json is missing {missing}")
    updater = data["min_updater"]
    if isinstance(updater, bool) or not isinstance(updater, int) or updater < 1:
        raise MetadataError("release/compatibility.json: min_updater must be a whole number, 1 or more")
    semver.parse(data["min_from_version"])
    stones = data["stepping_stones"]
    if not isinstance(stones, list) or [semver.parse(s) for s in stones] != sorted({semver.parse(s) for s in stones}):
        raise MetadataError("release/compatibility.json: stepping_stones must be versions, in order, without repeats")
    if not isinstance(data["compose_min"], str) or not manifest.COMPOSE.fullmatch(data["compose_min"]):
        raise MetadataError("release/compatibility.json: compose_min must look like 2.23.0")
    return {k: data[k] for k in ("min_updater", "min_from_version", "stepping_stones", "compose_min")}


def check_withdrawn_history(path: str, repo_dir: Path) -> list:
    """Every committed version of release/withdrawn.json, oldest first, plus the working copy:
    each must be valid, and its revision must go up whenever its content changes (and never
    down). Needs the full git history (refuses a shallow clone). Returns the revisions seen."""
    import subprocess

    from . import withdrawn

    def git(*args):
        res = subprocess.run(["git", *args], cwd=repo_dir, capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0:
            raise MetadataError(f"git {args[0]} failed: {res.stderr.strip()[:200]}")
        return res.stdout

    if git("rev-parse", "--is-shallow-repository").strip() == "true":
        raise MetadataError("the withdrawal-list history needs the full git history (fetch-depth: 0)")
    versions = []
    for commit in reversed(git("log", "--format=%H", "--", path).split()):
        text = git("show", f"{commit}:{path}")
        versions.append((commit[:12], text))
    current = Path(repo_dir, path).read_text(encoding="utf-8")
    if not versions or versions[-1][1] != current:
        versions.append(("working copy", current))
    seen, previous = [], None
    for where, text in versions:
        try:
            parsed = withdrawn.validate(json.loads(text))
        except ValueError:
            raise MetadataError(f"release/withdrawn.json at {where} isn't valid JSON") from None
        if previous is not None:
            if parsed.revision < previous.revision:
                raise MetadataError(f"release/withdrawn.json went back to revision {parsed.revision} at {where}")
            if parsed.digest != previous.digest and parsed.revision == previous.revision:
                raise MetadataError(f"release/withdrawn.json changed at {where} without a new revision "
                                    f"(still {parsed.revision})")
        seen.append(parsed.revision)
        previous = parsed
    return seen


def generate(*, version: str, commit: str, run_id: str, index_digest: str, index_raw: bytes, describe: dict,
             bundle_path: Path, compatibility: dict, linux_bundle_path: Path | None = None) -> dict:
    """The release manifest (a dict), checked by manifest.validate. MetadataError on any inconsistency."""
    parsed = semver.parse(version)
    if not isinstance(describe, dict) or describe.get("schema") != "alertbot-describe/1":
        raise MetadataError("the image description isn't from describe_release")
    if describe.get("problems"):
        raise MetadataError(f"the image reports problems: {describe['problems']}"[:300])
    if describe.get("version") != version:
        raise MetadataError(f"the image says it is {str(describe.get('version'))[:40]!r}, not {version}")
    platforms = platforms_from_index(index_raw, index_digest)
    if describe.get("platform") not in platforms:
        raise MetadataError(f"the image was described on {describe.get('platform')!r}, which isn't one of its platforms")
    bundles = describe.get("bundles") or {}
    image_files = bundles.get("windows", {}).get("files")
    if not isinstance(image_files, dict) or not image_files:
        raise MetadataError("the image description lists no deployment files")
    linux_files = bundles.get("linux", {}).get("files")
    if (linux_bundle_path is None) != (not linux_files):
        raise MetadataError("the image's Linux deployment files and the Linux download must come together")

    bundle_path = Path(bundle_path)
    expected_name = manifest.BUNDLE_NAME.format(version=version)
    if bundle_path.name != expected_name:
        raise MetadataError(f"the download must be named {expected_name}")
    zip_files = read_zip(bundle_path)
    _check_download(zip_files, image_files, COMPOSE_ENV, version, index_digest, "download")
    artifacts = {"windows-bundle": {"name": expected_name, "sha256": sha256(bundle_path.read_bytes()),
                                    "size": bundle_path.stat().st_size,
                                    "files": {n: sha256(d) for n, d in sorted(zip_files.items())}}}
    if linux_bundle_path is not None:
        linux_bundle_path = Path(linux_bundle_path)
        linux_name = manifest.LINUX_BUNDLE_NAME.format(version=version)
        if linux_bundle_path.name != linux_name:
            raise MetadataError(f"the Linux download must be named {linux_name}")
        tar_files = read_tar(linux_bundle_path)
        if COMPOSE_ENV in tar_files:
            raise MetadataError(f"the Linux download must not contain {COMPOSE_ENV} (an install's own settings)")
        _check_download(tar_files, linux_files, RELEASE_ENV, version, index_digest, "Linux download")
        artifacts["linux-bundle"] = {"name": linux_name, "sha256": sha256(linux_bundle_path.read_bytes()),
                                     "size": linux_bundle_path.stat().st_size,
                                     "files": {n: sha256(d) for n, d in sorted(tar_files.items())}}

    data = {
        "schema": manifest.SCHEMA,
        "version": version,
        "channel": "prerelease" if parsed.is_prerelease else "stable",
        "source": {"repository": REPOSITORY, "commit": commit, "run_id": str(run_id)},
        "image": {"name": IMAGE, "digest": index_digest, "platforms": platforms},
        "compatibility": {**read_compatibility(compatibility), "data_schema": describe.get("data_schema")},
        "artifacts": artifacts,
        "notes_url": f"https://github.com/{REPOSITORY}/releases/tag/v{version}",
    }
    manifest.validate(data)  # the same check every updater makes
    return data


def dump(data: dict) -> bytes:
    """The manifest as published: sorted keys, two-space indent, LF, so it is reproducible."""
    return (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def checksums(folder: Path, names) -> bytes:
    """SHA256SUMS for these files in folder: "<sha256>  <name>" lines, sorted by name."""
    lines = []
    for name in sorted(set(names)):
        if "/" in name or "\\" in name or name in ("", ".", ".."):
            raise MetadataError(f"a checksum entry must be a plain file name: {name!r}")
        lines.append(f"{sha256((Path(folder) / name).read_bytes())}  {name}\n")
    return "".join(lines).encode("utf-8")


def verify_checksums(folder: Path) -> list:
    """Check every file listed in folder/SHA256SUMS. Returns the names; MetadataError if one is
    missing, changed, listed twice, or the file is malformed."""
    folder = Path(folder)
    try:
        text = (folder / CHECKSUMS).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise MetadataError(f"{CHECKSUMS} can't be read") from None
    names = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\]+)", line)
        if not match:
            raise MetadataError(f"{CHECKSUMS} line {number} is malformed")
        digest, name = match.groups()
        if name in names:
            raise MetadataError(f"{CHECKSUMS} lists {name} twice")
        path = folder / name
        if not path.is_file():
            raise MetadataError(f"{name} is listed in {CHECKSUMS} but missing")
        if sha256(path.read_bytes()) != digest:
            raise MetadataError(f"{name} doesn't match {CHECKSUMS}")
        names.append(name)
    if not names:
        raise MetadataError(f"{CHECKSUMS} lists nothing")
    return names


def write_release_files(out: Path, data: dict, bundle_path: Path, linux_bundle_path: Path | None = None) -> tuple:
    """Write alertbot-release.json and SHA256SUMS (covering the downloads and the manifest) into out,
    next to the downloads. Returns their paths."""
    out = Path(out)
    downloads = [Path(bundle_path)] + ([Path(linux_bundle_path)] if linux_bundle_path else [])
    if any(d.resolve().parent != out.resolve() for d in downloads):
        raise MetadataError("the downloads must be in the output folder")
    manifest_path = out / MANIFEST_ASSET
    manifest_path.write_bytes(dump(data))
    sums_path = out / CHECKSUMS
    sums_path.write_bytes(checksums(out, [d.name for d in downloads] + [MANIFEST_ASSET]))
    verify_checksums(out)
    return manifest_path, sums_path
