"""Building a release's manifest and checksums from what was actually built. See docs/RELEASES.md.

    python -m release_meta generate --version 1.2.0 --commit <sha> --run-id <id> \\
        --index-digest sha256:<...> --index index.json --describe describe.json \\
        --bundle dist/BlueskyDiscordAlertBot-1.2.0.zip --compatibility release/compatibility.json --out dist
    python -m release_meta verify-checksums dist

Inputs, each checked before anything is written:
- the image index (`docker buildx imagetools inspect --raw <image>@<digest>`): its SHA-256 must be
  the digest given, so the platform list really belongs to that digest;
- what the image says about itself (`describe`, run by digest): its version must be the release's,
  its deployment files must be complete;
- the download (zip): exactly the image's deployment files with the same hashes, plus
  docker/compose.env pinning this image **by digest**;
- release/compatibility.json (min_updater, min_from_version, stepping_stones, compose_min).

The result must pass release_meta.manifest.validate, the same check updaters make. Output:
alertbot-release.json and SHA256SUMS (the zip and the manifest), both byte-for-byte reproducible
from the same inputs.

What this proves is integrity (the manifest describes these exact bytes); who published it is a
separate question, answered by where it is published (docs/RELEASES.md, section 1).
"""
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

from . import IMAGE, MANIFEST_ASSET, REPOSITORY, MetadataError, manifest, semver
from .bundle import NEVER_BUNDLED

COMPAT_SCHEMA = "alertbot-compatibility/1"
INDEX_TYPES = ("application/vnd.oci.image.index.v1+json",
               "application/vnd.docker.distribution.manifest.list.v2+json")
COMPOSE_ENV = "docker/compose.env"
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


def read_compatibility(data) -> dict:
    if not isinstance(data, dict) or data.get("schema") != COMPAT_SCHEMA:
        raise MetadataError("release/compatibility.json has an unknown schema")
    allowed = {"schema", "min_updater", "min_from_version", "stepping_stones", "compose_min"}
    unknown = set(data) - allowed
    if unknown:
        raise MetadataError(f"release/compatibility.json has unknown fields: {sorted(unknown)}")
    return {k: data[k] for k in ("min_updater", "min_from_version", "stepping_stones", "compose_min") if k in data}


def generate(*, version: str, commit: str, run_id: str, index_digest: str, index_raw: bytes, describe: dict,
             bundle_path: Path, compatibility: dict) -> dict:
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
    image_files = (describe.get("bundles") or {}).get("windows", {}).get("files")
    if not isinstance(image_files, dict) or not image_files:
        raise MetadataError("the image description lists no deployment files")

    bundle_path = Path(bundle_path)
    expected_name = manifest.BUNDLE_NAME.format(version=version)
    if bundle_path.name != expected_name:
        raise MetadataError(f"the download must be named {expected_name}")
    zip_files = read_zip(bundle_path)
    if COMPOSE_ENV not in zip_files:
        raise MetadataError(f"the download has no {COMPOSE_ENV}")
    pinned = re.findall(r"(?m)^ALERTBOT_IMAGE=(.*?)\r?$", zip_files[COMPOSE_ENV].decode("utf-8", "replace"))
    allowed_pins = {f"{IMAGE}@{index_digest}", f"{IMAGE}:{version}@{index_digest}"}
    if len(pinned) != 1 or pinned[0] not in allowed_pins:
        raise MetadataError("the download's docker/compose.env must pin this image by its digest "
                            f"({IMAGE}:{version}@{index_digest})")
    others = {n: d for n, d in zip_files.items() if n != COMPOSE_ENV}
    if set(others) != set(image_files):
        raise MetadataError(f"the download's files differ from the image's: missing {sorted(set(image_files) - set(others))}, "
                            f"unexpected {sorted(set(others) - set(image_files))}"[:300])
    for name, data in others.items():
        if sha256(data) != image_files[name]:
            raise MetadataError(f"{name} in the download differs from the image's")
    leaked = [n for n in zip_files if n in NEVER_BUNDLED[1:] or (n.endswith(".env") and n != COMPOSE_ENV)]
    if leaked:
        raise MetadataError(f"the download must not contain {leaked}")

    data = {
        "schema": manifest.SCHEMA,
        "version": version,
        "channel": "prerelease" if parsed.is_prerelease else "stable",
        "source": {"repository": REPOSITORY, "commit": commit, "run_id": str(run_id)},
        "image": {"name": IMAGE, "digest": index_digest, "platforms": platforms},
        "compatibility": {**read_compatibility(compatibility), "data_schema": describe.get("data_schema")},
        "artifacts": {"windows-bundle": {"name": expected_name, "sha256": sha256(bundle_path.read_bytes()),
                                         "size": bundle_path.stat().st_size,
                                         "files": {n: sha256(d) for n, d in sorted(zip_files.items())}}},
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


def write_release_files(out: Path, data: dict, bundle_path: Path) -> tuple:
    """Write alertbot-release.json and SHA256SUMS (covering the download and the manifest) into out,
    next to the download. Returns their paths."""
    out = Path(out)
    if Path(bundle_path).resolve().parent != out.resolve():
        raise MetadataError("the download must be in the output folder")
    manifest_path = out / MANIFEST_ASSET
    manifest_path.write_bytes(dump(data))
    sums_path = out / CHECKSUMS
    sums_path.write_bytes(checksums(out, [Path(bundle_path).name, MANIFEST_ASSET]))
    verify_checksums(out)
    return manifest_path, sums_path
