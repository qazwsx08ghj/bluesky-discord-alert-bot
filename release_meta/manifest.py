"""The release manifest (alertbot-release.json), one per published version. See docs/RELEASES.md.

validate(data)                  the manifest on its own: schema, types, formats, internal consistency
check_against_release(m, rel)   the manifest against the GitHub release it came from

Both raise MetadataError with a plain reason. A manifest that fails either check is never used.

What a valid manifest proves, and what it doesn't: its hashes and digests let anyone check that
the bytes they downloaded are the ones the manifest names (integrity). They don't prove who wrote
the manifest (authenticity): that comes from where it is read (an immutable GitHub release of
this repository, over HTTPS), see docs/RELEASES.md, "Integrity is not authenticity".

Additive changes keep the schema id: unknown fields are ignored. An incompatible change gets a
new major id (alertbot-release/2), which this code refuses.
"""
import re
from dataclasses import dataclass

from . import IMAGE, MANIFEST_ASSET, REPOSITORY, MetadataError, NeedsNewerUpdater, semver

SCHEMA = "alertbot-release/1"
SCHEMA_FAMILY = "alertbot-release/"
CHANNELS = ("stable", "prerelease")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
MIGRATION = re.compile(r"^\d{4}_[a-z0-9_]+$")
COMPOSE = re.compile(r"^\d+\.\d+\.\d+$")
BUNDLE_NAME = "BlueskyDiscordAlertBot-{version}.zip"
# Files every Windows download must contain: the updater (M3) installs exactly these.
WINDOWS_FILES = ("setup.bat", "start.bat", "update.bat", "stop.bat", "backup.bat", "restore.bat",
                 "docker-compose.yml", "docker/scripts/common.bat")
LINUX_BUNDLE_NAME = "BlueskyDiscordAlertBot-{version}-linux.tar.gz"
# Files every Linux deployment-file set must contain (./alertbot update installs these), and the
# download besides (it adds docker/release.env, the image pinned by digest).
LINUX_FILES = ("alertbot", "docker-compose.yml")
# The downloads this version checks completely: kind -> (name, required files). Any other kind
# is accepted with only its shape checked, so later versions can add downloads.
BUNDLES = {"windows-bundle": (BUNDLE_NAME, WINDOWS_FILES),
           "linux-bundle": (LINUX_BUNDLE_NAME, LINUX_FILES + ("docker/release.env",))}
MAX_BYTES = 256 * 1024


@dataclass(frozen=True)
class Manifest:
    version: semver.Version
    channel: str
    image_digest: str
    min_updater: int
    min_from_version: semver.Version
    stepping_stones: tuple
    data: dict  # the whole manifest, as read

    @property
    def image_reference(self) -> str:
        """What to pull: by digest only (the tag is for people, not for deciding)."""
        return f"{IMAGE}@{self.image_digest}"


def _need(obj, key, kind, where):
    if not isinstance(obj, dict) or key not in obj:
        raise MetadataError(f"{where}: {key!r} is missing")
    value = obj[key]
    if kind is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise MetadataError(f"{where}.{key} must be a whole number")
    elif not isinstance(value, kind):
        raise MetadataError(f"{where}.{key} has the wrong type")
    return value


def _match(pattern, value, what):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise MetadataError(f"{what} is malformed: {value!r}"[:200])
    return value


def validate(data) -> Manifest:
    """A Manifest from parsed JSON, or MetadataError."""
    if not isinstance(data, dict):
        raise MetadataError("the manifest isn't a JSON object")
    schema = _need(data, "schema", str, "manifest")
    if schema != SCHEMA:
        if schema.startswith(SCHEMA_FAMILY):
            raise NeedsNewerUpdater(f"the manifest uses {schema}"[:120] + ", which this version of the updater "
                                    "doesn't know; a newer updater is needed")
        raise MetadataError(f"this isn't a release manifest (schema {schema!r})"[:200])

    version = semver.parse(_need(data, "version", str, "manifest"))
    channel = _need(data, "channel", str, "manifest")
    if channel not in CHANNELS:
        raise MetadataError(f"unknown channel {channel!r}"[:100])
    if (channel == "prerelease") != version.is_prerelease:
        raise MetadataError(f"version {version} can't be on the {channel} channel")

    source = _need(data, "source", dict, "manifest")
    if _need(source, "repository", str, "source") != REPOSITORY:
        raise MetadataError("the manifest names another repository")
    _match(COMMIT, _need(source, "commit", str, "source"), "source.commit")
    run_id = _need(source, "run_id", str, "source")
    if not run_id.isdigit():
        raise MetadataError("source.run_id is malformed")

    image = _need(data, "image", dict, "manifest")
    if _need(image, "name", str, "image") != IMAGE:
        raise MetadataError("the manifest names another image")
    digest = _match(DIGEST, _need(image, "digest", str, "image"), "image.digest")
    platforms = _need(image, "platforms", dict, "image")
    if "linux/amd64" not in platforms:
        raise MetadataError("image.platforms must include linux/amd64")
    for platform, platform_digest in platforms.items():
        _match(DIGEST, platform_digest, f"image.platforms[{platform}]")
        if platform_digest == digest:
            raise MetadataError("a platform digest can't equal the image (index) digest")

    compat = _need(data, "compatibility", dict, "manifest")
    min_updater = _need(compat, "min_updater", int, "compatibility")
    if min_updater < 1:
        raise MetadataError("compatibility.min_updater must be 1 or more")
    min_from = semver.parse(_need(compat, "min_from_version", str, "compatibility"))
    if min_from > version:
        raise MetadataError("compatibility.min_from_version is newer than the version itself")
    stones = [semver.parse(s) for s in _need(compat, "stepping_stones", list, "compatibility")]
    if stones != sorted(set(stones)):
        raise MetadataError("compatibility.stepping_stones must be in order, without repeats")
    if any(s >= version for s in stones):
        raise MetadataError("a stepping stone must be older than the version itself")
    schema_map = _need(compat, "data_schema", dict, "compatibility")
    if not schema_map:
        raise MetadataError("compatibility.data_schema is empty")
    for app, migration in schema_map.items():
        if not isinstance(app, str) or not re.fullmatch(r"[a-z_]+", app):
            raise MetadataError(f"compatibility.data_schema has a malformed app name {app!r}"[:120])
        _match(MIGRATION, migration, f"compatibility.data_schema[{app}]")
    _match(COMPOSE, _need(compat, "compose_min", str, "compatibility"), "compatibility.compose_min")

    artifacts = _need(data, "artifacts", dict, "manifest")
    if not artifacts:
        raise MetadataError("the manifest lists no downloads")
    for kind, artifact in artifacts.items():
        if kind in BUNDLES:
            _check_bundle(kind, artifact, version)
        else:  # kinds this version doesn't use: only their shape is checked
            _need(artifact, "name", str, f"artifacts[{kind}]")
            _match(SHA256, _need(artifact, "sha256", str, f"artifacts[{kind}]"), f"artifacts[{kind}].sha256")

    notes = _need(data, "notes_url", str, "manifest")
    if notes != f"https://github.com/{REPOSITORY}/releases/tag/v{version}":
        raise MetadataError("notes_url doesn't point to this version's release page")

    return Manifest(version=version, channel=channel, image_digest=digest, min_updater=min_updater,
                    min_from_version=min_from, stepping_stones=tuple(stones), data=data)


def _check_bundle(kind, bundle, version):
    where = f"artifacts[{kind}]"
    name_format, required = BUNDLES[kind]
    if _need(bundle, "name", str, where) != name_format.format(version=version):
        raise MetadataError(f"{where}.name doesn't match the version")
    _match(SHA256, _need(bundle, "sha256", str, where), f"{where}.sha256")
    if _need(bundle, "size", int, where) <= 0:
        raise MetadataError(f"{where}.size must be positive")
    files = _need(bundle, "files", dict, where)
    for name in required:
        if name not in files:
            raise MetadataError(f"{where}.files is missing {name}")
    for name, digest in files.items():
        if not isinstance(name, str) or name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
            raise MetadataError(f"{where}.files has an unsafe path {name!r}"[:120])
        _match(SHA256, digest, f"{where}.files[{name}]")


@dataclass(frozen=True)
class ReleaseRecord:
    """What GitHub's Releases API says about one release (only the fields used here)."""
    tag: str
    draft: bool
    prerelease: bool
    immutable: bool
    assets: dict  # name -> {"size": int, "digest": "sha256:..." or None, "url": str}


def check_against_release(manifest: Manifest, release: ReleaseRecord, require_immutable: bool = True):
    """The manifest must describe the release it was attached to. MetadataError if not."""
    if release.draft:
        raise MetadataError(f"{release.tag} is a draft")
    if release.tag != f"v{manifest.version}":
        raise MetadataError(f"the manifest says {manifest.version}, but it is attached to {release.tag}")
    if release.prerelease != (manifest.channel == "prerelease"):
        raise MetadataError(f"{release.tag}: GitHub's pre-release flag doesn't match the manifest's channel")
    if require_immutable and not release.immutable:
        raise MetadataError(f"{release.tag} isn't an immutable release, so its files could have been changed "
                            "after publishing")
    if MANIFEST_ASSET not in release.assets:
        raise MetadataError(f"{release.tag} has no {MANIFEST_ASSET}")
    for kind, artifact in manifest.data["artifacts"].items():
        asset = release.assets.get(artifact["name"])
        if asset is None:
            raise MetadataError(f"{release.tag} is missing {artifact['name']}, which its manifest lists")
        if "size" in artifact and asset.get("size") != artifact["size"]:
            raise MetadataError(f"{release.tag}: {artifact['name']} has a different size than the manifest says")
        github_digest = asset.get("digest")
        if github_digest and github_digest != f"sha256:{artifact['sha256']}":
            raise MetadataError(f"{release.tag}: {artifact['name']} has a different hash than the manifest says")
