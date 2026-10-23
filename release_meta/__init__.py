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

# The updater protocol this code understands. A release whose manifest asks for a higher
# compatibility.min_updater can't be installed by it (the updater must be updated first).
UPDATER_PROTOCOL = 1


class MetadataError(Exception):
    """Release metadata is missing, unreadable, invalid or inconsistent. Nothing may be installed."""
