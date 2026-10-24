"""Reading the published releases and the withdrawal list from GitHub. Read-only; no login.

load_candidates()   every published release, each with its manifest or the reason it is unusable
load_withdrawn()    release/withdrawn.json from the main branch
check(...)          both, then select.decide

Where trust comes from (docs/RELEASES.md, "Integrity is not authenticity"): the repository is
fixed in the code, every request is HTTPS to GitHub's own hosts (certificates checked by Python),
and a manifest is used only from an immutable release. Anything that can't be read, is too
large, is malformed or doesn't match its release raises MetadataError: the caller installs
nothing. (Unauthenticated, GitHub allows 60 requests an hour per address; a check uses about
two plus one per release with a newer version.)

Tests point it at a local fake server: base URLs must otherwise be GitHub's.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

from . import (LEGACY_VERSIONS, MANIFEST_ASSET, REPOSITORY, WITHDRAWN_PATH, MetadataError, NeedsNewerUpdater,
               manifest, select, semver, withdrawn)

API = "https://api.github.com"
# Where a release download may be served from after GitHub's redirect.
ALLOWED_HOSTS = {"api.github.com", "github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com"}
TIMEOUT = 15
MAX_PAGES = 5
MAX_LIST_BYTES = 4 * 1024 * 1024


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed):
        super().__init__()
        self.allowed = allowed

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed(newurl, self.allowed):
            raise MetadataError("a download was redirected to an unexpected address")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _allowed(url, allowed):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" and parts.hostname in ALLOWED_HOSTS:
        return True
    return url.startswith(tuple(allowed))  # tests only: the fake server's own address


class Client:
    def __init__(self, api: str = API, repository: str = REPOSITORY, test_hosts=()):
        """test_hosts: extra URL prefixes allowed, only http://127.0.0.1:<port>/ (the tests' fake GitHub),
        or, in an image built for the tests only (is_test_build), any http:// test server."""
        from . import is_test_build

        for host in test_hosts:
            if not host.startswith("http://127.0.0.1:") and not (is_test_build() and host.startswith("http://")):
                raise ValueError("only a local test server may be added")
        if not _allowed(api.rstrip("/") + "/", test_hosts):
            raise ValueError("the API must be GitHub's")
        self.api, self.repository, self.test_hosts = api.rstrip("/"), repository, tuple(test_hosts)
        self.opener = urllib.request.build_opener(_SafeRedirects(self.test_hosts))

    def _get(self, url, accept, limit):
        if not _allowed(url, self.test_hosts):
            raise MetadataError("refused to read release information from an unexpected address")
        request = urllib.request.Request(url, headers={
            "Accept": accept, "User-Agent": "bluesky-discord-alert-bot-updater", "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with self.opener.open(request, timeout=TIMEOUT) as response:
                body = response.read(limit + 1)
                link = response.headers.get("Link", "")
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                raise MetadataError("GitHub is limiting requests right now; try again later") from None
            raise MetadataError(f"GitHub answered {exc.code}") from None
        except MetadataError:
            raise
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise MetadataError(f"GitHub couldn't be reached ({type(exc).__name__})") from None
        if len(body) > limit:
            raise MetadataError("the release information is larger than allowed")
        return body, link

    def _json(self, url, accept="application/vnd.github+json", limit=MAX_LIST_BYTES):
        body, link = self._get(url, accept, limit)
        try:
            return json.loads(body.decode("utf-8")), link
        except (UnicodeDecodeError, ValueError):
            raise MetadataError("the release information isn't valid JSON") from None

    def releases(self) -> list:
        url, found = f"{self.api}/repos/{self.repository}/releases?per_page=100", []
        for _ in range(MAX_PAGES):
            page, link = self._json(url)
            if not isinstance(page, list):
                raise MetadataError("GitHub's list of releases is malformed")
            found += page
            nxt = next((p.split(";")[0].strip(" <>") for p in link.split(",") if 'rel="next"' in p), None)
            if not nxt:
                return found
            url = nxt
        raise MetadataError("there are more releases than this updater reads")

    def asset_json(self, url, limit):
        data, _ = self._json(url, accept="application/octet-stream", limit=limit)
        return data

    def release(self, tag: str) -> dict:
        if not tag.startswith("v") or not semver.is_valid(tag[1:]):
            raise MetadataError(f"{tag!r} isn't a version tag")
        data, _ = self._json(f"{self.api}/repos/{self.repository}/releases/tags/{tag}")
        if not isinstance(data, dict):
            raise MetadataError("GitHub's description of the release is malformed")
        return data

    def asset_bytes(self, url, limit) -> bytes:
        body, _ = self._get(url, "application/octet-stream", limit)
        return body

    def withdrawn_file(self):
        url = f"{self.api}/repos/{self.repository}/contents/{WITHDRAWN_PATH}?ref=main"
        data, _ = self._json(url, accept="application/vnd.github.raw+json", limit=withdrawn.MAX_BYTES)
        return data


def _record(raw) -> manifest.ReleaseRecord:
    try:
        assets = {a["name"]: {"size": a.get("size"), "digest": a.get("digest"), "url": a["browser_download_url"]}
                  for a in raw.get("assets", [])}
        return manifest.ReleaseRecord(tag=raw["tag_name"], draft=bool(raw.get("draft")),
                                      prerelease=bool(raw.get("prerelease")),
                                      immutable=raw.get("immutable") is True, assets=assets)
    except (KeyError, TypeError, AttributeError):
        raise MetadataError("GitHub's description of a release is malformed") from None


def load_candidates(client: Client, newer_than: semver.Version | None = None,
                    require_immutable: bool = True) -> list:
    """Every published (non-draft) release as a select.Candidate, except the LEGACY_VERSIONS.

    Manifests are only downloaded for releases newer than newer_than (the others can't be
    chosen anyway). A release whose tag isn't a version tag is ignored; one whose manifest can't
    be used is kept, with the problem, so the choice can refuse to go past it.
    """
    candidates = []
    for raw in client.releases():
        record = _record(raw)
        if record.draft or not record.tag.startswith("v") or not semver.is_valid(record.tag[1:]):
            continue
        version = semver.parse(record.tag[1:])
        if str(version) in LEGACY_VERSIONS:
            continue  # published before release metadata; never installable, never blocking
        if newer_than is not None and version <= newer_than:
            candidates.append(select.Candidate(version, problem="older than the installed version (not read)"))
            continue
        try:
            asset = record.assets.get(MANIFEST_ASSET)
            if asset is None:
                raise MetadataError(f"{record.tag} has no {MANIFEST_ASSET}")
            parsed = manifest.validate(client.asset_json(asset["url"], manifest.MAX_BYTES))
            manifest.check_against_release(parsed, record, require_immutable=require_immutable)
            candidates.append(select.Candidate(version, manifest=parsed))
        except NeedsNewerUpdater as exc:
            candidates.append(select.Candidate(version, problem=str(exc), needs_newer_updater=True))
        except MetadataError as exc:
            candidates.append(select.Candidate(version, problem=str(exc)))
    return candidates


def load_withdrawn(client: Client) -> withdrawn.Withdrawn:
    return withdrawn.validate(client.withdrawn_file())


def verify_release(client: Client, tag: str, expected_manifest: bytes | None = None) -> manifest.Manifest:
    """A published release, read back from GitHub the way updaters read it: it must be immutable, its
    manifest valid and matching the release, and (if given) byte-for-byte the manifest that was built."""
    record = _record(client.release(tag))
    asset = record.assets.get(MANIFEST_ASSET)
    if asset is None:
        raise MetadataError(f"{tag} has no {MANIFEST_ASSET}")
    raw = client.asset_bytes(asset["url"], manifest.MAX_BYTES)
    try:
        parsed = manifest.validate(json.loads(raw.decode("utf-8")))
    except (UnicodeDecodeError, ValueError):
        raise MetadataError(f"{tag}'s {MANIFEST_ASSET} isn't valid JSON") from None
    manifest.check_against_release(parsed, record, require_immutable=True)
    if expected_manifest is not None and raw != expected_manifest:
        raise MetadataError(f"{tag}'s published {MANIFEST_ASSET} isn't the one that was built")
    return parsed


def check(installed: str, channel: str, client: Client | None = None, seen: withdrawn.Seen | None = None,
          updater_protocol: int | None = None) -> select.Decision:
    """The whole check: read everything, then decide. Any failure to read is a blocked decision."""
    client = client or Client()
    try:
        current = semver.parse(installed)
    except MetadataError:
        current = None
    try:
        withdrawn_list = load_withdrawn(client)
        candidates = load_candidates(client, newer_than=current)
    except MetadataError as exc:
        return select.Decision(select.BLOCKED, "metadata_unavailable",
                               f"Couldn't check for updates: {exc}. Nothing was changed.")
    kwargs = {} if updater_protocol is None else {"updater_protocol": updater_protocol}
    return select.decide(installed, channel, candidates, withdrawn_list, seen=seen, **kwargs)
