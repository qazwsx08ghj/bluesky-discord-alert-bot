# Releases: metadata, trust and publishing

For maintainers. How a published version is described (the release manifest), which version an install may move to, how a bad release is withdrawn, and how releases are meant to be published.

**Status (M2, foundation):**
- **Done:** the metadata rules in `release_meta/` (standard library only) and their tests, and `release/withdrawn.json`.
- **Not changed yet:** the publishing workflow (`.github/workflows/release.yml`) and the GitHub settings. Today's publishing is described in [DOCKER.md](DOCKER.md), section 7. Section 5 below is the planned flow, to be built in a later step after approval.
- **Nothing installs releases automatically yet:** that is the updater (M3).

## 1. Integrity is not authenticity

Two different questions, answered by different things:

| Question | Answered by | What it can't do |
|---|---|---|
| **Integrity:** are these the bytes the manifest describes? | The manifest's SHA-256 hashes (download, every file in it) and image digests; `SHA256SUMS` | Prove who wrote the manifest. Anyone who can replace a manifest can also put matching hashes in it. |
| **Authenticity:** did this project publish it? | Where the manifest is read from: an **immutable** GitHub release of **this repository** (fixed in the code), over HTTPS to GitHub's own hosts, published by the release workflow after the maintainer's approval | Protect against a takeover of the maintainer's GitHub account, which could publish a release itself. Two-factor login or a passkey on that account is the root of trust (Design 1: no extra signing keys). |

**Build provenance attestations** (planned with the workflow) add a check anyone can run by hand (`gh attestation verify`): the image or download was built by this repository's workflow, from a given commit. The updater doesn't depend on them.

**What the updater relies on, in order:**
1. **The address:** the repository and image names are in the code (`release_meta/__init__.py`), never in a downloaded file. Only HTTPS to `api.github.com`, `github.com` and GitHub's download hosts is allowed, redirects included (`release_meta/github.py`).
2. **The release is immutable:** a release that isn't immutable is refused, so its files can't have been changed after publishing. Releases published before immutable releases were switched on (1.0.0-rc.1 and rc.2) are never chosen; they are older than any version an updater will run anyway.
3. **Consistency:**
   - the manifest must validate (`release_meta/manifest.py`);
   - it must describe the release it is attached to: tag, pre-release flag, assets, and the sizes and hashes GitHub reports;
   - it must name this repository and this image.
4. **Integrity of what is downloaded:** the image is pulled **by digest** (`Manifest.image_reference`), never by tag, and the download and its files are checked against the manifest's hashes (M3).

## 2. The release manifest (`alertbot-release.json`)

One per release, attached to it. Schema id `alertbot-release/1`. Example (values shortened):

```json
{
  "schema": "alertbot-release/1",
  "version": "1.0.0",
  "channel": "stable",
  "source": {"repository": "qazwsx08ghj/bluesky-discord-alert-bot", "commit": "<40 hex>", "run_id": "<digits>"},
  "image": {"name": "ghcr.io/qazwsx08ghj/bluesky-discord-alert-bot",
            "digest": "sha256:<64 hex>",
            "platforms": {"linux/amd64": "sha256:<64 hex>", "linux/arm64": "sha256:<64 hex>"}},
  "compatibility": {"min_updater": 1, "min_from_version": "1.0.0-rc.1", "stepping_stones": [],
                    "data_schema": {"bluesky_monitor": "0009_remove_single_channel_fields", "...": "..."},
                    "compose_min": "2.23.0"},
  "artifacts": {"windows-bundle": {"name": "BlueskyDiscordAlertBot-1.0.0.zip", "sha256": "<64 hex>",
                                   "size": 12345, "files": {"update.bat": "<64 hex>", "...": "..."}}},
  "notes_url": "https://github.com/qazwsx08ghj/bluesky-discord-alert-bot/releases/tag/v1.0.0"
}
```

**Rules** (`release_meta/manifest.py`; anything else makes the manifest invalid):
- **`version`:** strict SemVer 2.0, no build metadata. **`channel`:** `prerelease` exactly when the version has a pre-release part.
- **`source.repository`, `image.name`:** this repository and its image, nothing else.
- **`image.digest`:** the multi-platform index digest, which is what is pulled. **`platforms`:** must include `linux/amd64`. All digests are `sha256:` plus 64 lowercase hex characters.
- **`compatibility`:**
  - `min_updater`: the updater protocol needed, 1 or more;
  - `min_from_version`: the oldest version that can update directly, not newer than the version itself;
  - `stepping_stones`: versions that must be installed on the way, ordered, without repeats, older than the version;
  - `data_schema`: the newest migration of each app;
  - `compose_min`: the minimum Docker Compose version.
- **`artifacts`:** at least one download.
  - `windows-bundle` must be named `BlueskyDiscordAlertBot-<version>.zip`, with its SHA-256, its size, and the hashes of its files, including every script the updater installs.
  - File paths must be relative, without `..`.
  - Other kinds, such as a future `linux-bundle`, need a name and a SHA-256.
- **`notes_url`:** this version's release page.

**Changes over time:** new fields may be added under the same schema id, and older updaters ignore them. An incompatible change gets `alertbot-release/2` and a higher `min_updater`. An updater that sees a schema it doesn't know refuses it and says a newer updater is needed.

**Against its GitHub release** (`check_against_release`):
- the tag is `v<version>`;
- GitHub's pre-release flag matches the channel;
- the release isn't a draft, and is immutable;
- `alertbot-release.json` and every listed download are attached;
- sizes and hashes match what GitHub reports.

## 3. Choosing a release

`release_meta/select.py`, `decide()`. The first rule that applies decides:

1. **The installed version must be a version.** A development build (`dev`) isn't updated automatically.
2. **The withdrawal list must not be older than one already seen** (see section 4).
3. **Candidates:** releases on the channel, newer than the installed version, not withdrawn.
   - Stable channel: final versions only.
   - Pre-release channel: everything, so an RC install moves on to the final release.
   - Order: SemVer 2.0, so `1.0.0-rc.9 < 1.0.0-rc.10 < 1.0.0`.
4. **Any invalid or inconsistent candidate blocks everything.** There is no fallback to an older release, because one bad release must not be able to steer installs backwards. The fix is to repair the release or withdraw it.
5. **No candidates:** up to date. If the installed version is withdrawn, the message says so and why. It is never moved to an older version.
6. **The target** is the newest candidate. Its `min_updater` must be met.
7. **The path:**
   - If the installed version is older than the target's `min_from_version`, the update goes through intermediate releases.
   - Stepping stones newer than the installed version are always installed on the way, even when a newer intermediate release exists.
   - Every step is valid, not withdrawn and on the channel.
   - With no path, nothing is installed and the message says a manual update is needed.

**Never older:** a version equal to or older than the installed one is never chosen. **Failing safely:** if the releases or the withdrawal list can't be read, are malformed, or don't match, the result is "blocked" and nothing is installed.

## 4. Withdrawn releases (`release/withdrawn.json`)

```json
{"schema": "alertbot-withdrawn/1", "revision": 2, "updated_at": "2026-11-02",
 "withdrawn": [{"version": "1.0.1", "withdrawn_at": "2026-11-02", "reason": "Plain words.", "replacement": "1.0.2"}]}
```

- **What withdrawing does:** the version is no longer offered. Its release, tag and image stay in place, so nothing is deleted and existing pins still work. An install already on that version sees a warning, and is offered a newer version if there is one. **It never triggers a downgrade.** If the withdrawn version damaged data, the fix is a newer release that repairs it.
- **Where it lives:** on `main`. Changing it needs a pull request with the required checks (your decision for M2). `release_meta.tests.WithdrawnTests.test_the_published_file_is_valid` checks it on every change.
- **Read from:** GitHub's API (`/repos/…/contents/release/withdrawn.json?ref=main`), not a CDN cache, at every check.
- **Strict:** an unknown schema, a malformed or repeated version, a date in the wrong format, an entry dated after `updated_at`, or a replacement that isn't newer or is itself withdrawn makes the whole file invalid. An entry for a version that was never released is allowed and changes nothing.

**Stale or inconsistent withdrawal information:**

| Situation | What the updater does |
|---|---|
| The file can't be read (network, 404, rate limit) | Installs nothing ("Couldn't check for updates") |
| The file is malformed or inconsistent | Installs nothing |
| `revision` is lower than the highest this install has seen | Installs nothing: the list may be stale or replayed (`withdrawn_stale`). The updater (M3) stores the highest revision it has seen. |
| A version is withdrawn after the check but before installing | The updater (M3) reads the list again right before installing, and stops if the target was withdrawn |
| An install that has never seen the list (first check) | Accepts any valid revision. A replayed old list can't be detected the first time; it can only hide withdrawals, never cause a downgrade, and every release it could offer is still immutable and checked. |

**`revision`:** goes up by 1 on every change and never down. The release workflow step (section 5) will also check this against the previous version of the file. For now it is checked only by the updater and by review.

## 5. Publishing (planned; not yet in `release.yml`)

**Rule: nothing becomes visible outside the repository before the maintainer approves the release.** That covers:
- pushing the image to GHCR;
- creating or moving a version tag;
- creating or publishing the GitHub release;
- moving `:stable`.

**1. Start.** Run the release workflow by hand (`workflow_dispatch`) on `main`, with the version as input. It is **no longer triggered by pushing a tag**: pushing a tag would itself be visible before any approval. The workflow creates the tag later, after approval.

**2. Prepare** (read-only token, no environment, nothing pushed):
- `release_check.py`: version, `package.json` and `CHANGELOG.md` agree;
- the version isn't released yet;
- the commit is on `main`;
- every CI job passes.

The job summary shows the version, the commit and the changelog section.

**3. Approval:** the publish job uses the `release` environment, and you are its required reviewer. Nothing below runs until you approve.

**4. Publish**, in this order. Any failure stops it, and nothing later happens.
1. Build the multi-platform image, and push it **by digest only, untagged**. Record the index digest and confirm it resolves.
2. Run the image by digest to read its version, its migrations and its bundled-file hashes (M2.2). This proves the image is the one just built.
3. Build the download pinned to `repo:VERSION@sha256:…`. Write the manifest and `SHA256SUMS`. **Validate the manifest with `release_meta`.**
4. Create build provenance attestations for the image digest and the download.
5. Create the version tag at the approved commit (blocked from being moved or deleted by the tag ruleset).
6. Create the GitHub release as a **draft**, upload the assets, download them again, and check their hashes against the manifest.
7. **Publish the release.** With immutable releases on, its tag and files are locked from then on. Then confirm through the public API that it reports `immutable: true` and that the manifest matches.
8. Add the version tag (`:X.Y.Z`) to the image digest.
9. **Final releases only, last:** point `:stable` at the digest. `:stable` is a convenience for people who run the image by hand. Installs and updates never use it.

**Also planned:**
- `concurrency: release`, so two releases never run at once;
- every action pinned to a full commit SHA;
- only the publish job gets write permissions (`contents`, `packages`, `id-token`, `attestations`).

**If publishing stops part-way:**
- before step 7, the release isn't published and no updater can see it. An untagged image or a draft may be left behind for the maintainer to remove.
- after step 7, the release is complete. A failure in steps 8–9 only affects the convenience tags, and can be repeated.

## 6. GitHub settings this relies on (to be applied by the maintainer; not changed yet)

1. **Immutable releases:** on. This applies to releases published after it is switched on.
2. **Tag ruleset for `refs/tags/v*`:** no updates, no deletions; creation limited to the release workflow and the maintainer. *Still to confirm when this is set up: how a ruleset exempts the workflow's token for creating tags.*
3. **`release` environment:** the maintainer as required reviewer; runs only from `main`.
4. **`main`:** pull requests required, and the required checks apply to administrators too.
5. **Actions:** full-SHA pinning required. Optionally, allow only GitHub-owned and listed actions.
6. **The maintainer's account:** two-factor login or a passkey.

## 7. Updaters, now and later

**Windows (M3):** the updater runs `release_meta` inside the bot's image. It:
- pulls by digest;
- installs the files listed under `windows-bundle`, checked against their hashes;
- uses `min_updater`, `min_from_version` and `stepping_stones` as above;
- stores the highest withdrawal `revision` it has seen.

**Ubuntu (later):** the same manifest, with a `linux-bundle` artifact added (no schema change), and the same `release_meta` code.

**Shared protocol:** both use `UPDATER_PROTOCOL` (now 1). A release that needs a newer updater says so in `min_updater`.

## 8. Tests

| Tests | Where | What |
|---|---|---|
| Rules (no network) | `release_meta/tests.py` | SemVer parsing and order; manifest validation, field by field; manifest against its release; `withdrawn.json` rules, including the published file; every selection rule; the command line |
| Against a fake GitHub | `release_meta/tests_github.py` | A local HTTP server answering like GitHub's API and downloads, with redirects and paging. It covers: an update found and pulled by digest; up to date; drafts ignored; withdrawal list missing, broken or stale; rate limits; GitHub unreachable; a mutable release; a manifest for another version or image; tampered size or hash; a wrong pre-release flag; no fallback past a bad release, unblocked by withdrawing it; a redirect to another site; an oversized manifest. The client refuses any address but GitHub's, plus a named `127.0.0.1` test server. |

Both run with `python manage.py test` (and so in CI), or without Django: `python -m unittest release_meta.tests release_meta.tests_github`.

**Command line**, standard library only:
```
python -m release_meta validate-manifest alertbot-release.json
python -m release_meta validate-withdrawn release/withdrawn.json
python -m release_meta check --installed 1.0.0-rc.2 --channel prerelease   # reads GitHub; changes nothing
```
