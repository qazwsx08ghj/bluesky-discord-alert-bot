"""Release metadata: what a published version is, and which one an install may move to.

Standard library only: the same code runs in the release workflow (checking what is about to be
published), in the bot's image (the updater, M3), and on a server (the Ubuntu updater, later).

- semver.py     SemVer 2.0 versions and their order
- manifest.py   the release manifest (alertbot-release.json): schema and validation
- withdrawn.py  release/withdrawn.json: versions taken out of discovery
- select.py     which release to install, if any (never older; fails closed)
- github.py     reading the published releases and withdrawn.json from GitHub (read-only)

The trust model, schemas and rules are described in docs/RELEASES.md.
"""

REPOSITORY = "qazwsx08ghj/bluesky-discord-alert-bot"
IMAGE = f"ghcr.io/{REPOSITORY}"
MANIFEST_ASSET = "alertbot-release.json"
WITHDRAWN_PATH = "release/withdrawn.json"

# Published before release metadata existed: mutable releases with no manifest. They can never be
# installed by an updater, and are passed over (not treated as broken releases) so that installs of
# them can still move to newer versions. Fixed here on purpose: nothing downloaded can add to it.
LEGACY_VERSIONS = frozenset({"1.0.0-rc.1", "1.0.0-rc.2"})

# The updater protocol this code understands. A release whose manifest asks for a higher
# compatibility.min_updater can't be installed by it (the updater must be updated first).
UPDATER_PROTOCOL = 1


class MetadataError(Exception):
    """Release metadata is missing, unreadable, invalid or inconsistent. Nothing may be installed."""


class NeedsNewerUpdater(MetadataError):
    """The metadata uses a format this updater doesn't know (a newer schema). It can't be read
    safely, so nothing may be installed from it; a newer updater has to be installed by hand."""


RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases"
