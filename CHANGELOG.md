# Changelog

All notable changes to this project are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**What the version numbers mean here:**
- **Major** (2.0.0): something you must act on, or a breaking API change. Examples: a field removed or renamed in the API (`docs/openapi.yaml`), a data change you have to do by hand, or a script that behaves differently.
- **Minor** (1.1.0): new features. Existing data, settings and API clients keep working, and `update.bat` is all you need.
- **Patch** (1.0.1): fixes only.

Every version is updated with `update.bat`, which backs up your data first.

## [Unreleased]

### Added
- **`restore.bat` says what a restore would undo before it changes anything:** accounts, channels and settings changed since the backup (by name; never links or passwords), posts noticed since then, and notifications that may then be sent again. If anything would be lost, `RESTORE` must be typed to go ahead. A backup made by a newer version is refused before anything changes.
- **The database is changed as a whole only when nothing else uses it:** the running bot holds a data lock that a migration or restore needs for itself. Before a restore, the scripts also check that no container (from any folder) still uses the data.
- **Docker Compose 2.23.0 (Docker Desktop 4.25) or later is required;** the scripts explain an older one instead of failing part-way.

### Changed
- **Stopping the checker is clean:** the Discord notification it is sending finishes and is saved, nothing new starts, a running Check All saves what it did, and it never waits more than 20 seconds. `update.bat` stops the old checker this way before the new version updates the database.
- **Backups are checked before they count:** one self-contained file, integrity-checked, and the copy in the `backups` folder compared with it. The backup taken before an update that changed the database is kept, however often `update.bat` or `backup.bat` runs afterwards.

### Fixed
- **A restore left two empty files** (`db.sqlite3.restore-check-wal`, `-shm`) in the data; journal files are now never left behind, and never applied to a file they don't belong to.
- **The documentation promised that a post is never sent twice.** That isn't possible with a Discord webhook: a notification interrupted by a crash or a lost answer may be sent again. The docs now say exactly when.

## [1.0.0] - Unreleased

The first public release. Compared with the earlier "black-window" version, the bot now runs in Docker Desktop and is set up and managed from a control panel in the browser.

### Added
- **One-click Windows install and management:** `setup.bat`, `start.bat`, `stop.bat`, `update.bat`, `backup.bat` and `restore.bat`. No Python, Node.js, Git or commands needed.
- **A control panel at http://127.0.0.1:8000:**
  - guided first-time setup;
  - accounts, with Check now, Pause / Resume and Check all;
  - an activity log with problems and their fixes;
  - settings;
  - light and dark themes.
- **Several named Discord channels,** a default channel, and a test message for each.
- **Several channels per account** (up to 10), each delivered and retried on its own: a channel that fails never holds up or repeats the others.
- **An older version never runs on newer data:** if the data was updated by a newer version, the scripts stop with a warning before replacing the running bot, and the bot itself shows a warning page instead of using the data. 1.0.0-rc.1 doesn't have this check. `update.bat` also names the folder the bot was run from before, if it was another one.
- **Optional role mention per channel:** paste a Discord Role ID to ping that role. Only that role can be pinged (`allowed_mentions`); `@everyone` and `@here` are refused, also when typed as a server ID.
- **Bluesky profile lookup** when adding an account, and a login test before the login is saved.
- **Per-account choice** of notifications for posts, reposts and replies.
- **Plain-language status and problems,** each with what to do about it.
- **Automatic backups** before every update and every database change; `restore.bat` to go back, which backs up the current data first.
- **Moving from the earlier version:** `setup.bat` brings over its accounts and settings, and only reads the old files.
- **For developers:**
  - an OpenAPI 3.1 description of the API (`docs/openapi.yaml`), with generated TypeScript types;
  - continuous integration;
  - a safe demo stack (`dev/docker-compose.yml`);
  - [CONTRIBUTING.md](CONTRIBUTING.md).
- **Documentation in English and Traditional Chinese (Taiwan)**, for users and contributors.
- **Licensing:** the MIT License, third-party license notices (`THIRD_PARTY_NOTICES.txt`, also linked in the panel), and a security policy.

### Changed
- **Since 1.0.0-rc.1:** an account's channel is now a list, `destinations`, in the API (`channel_id`, `channel` and `uses_default_channel` were removed; sending `channel_id` is refused with a pointer). Posts gain per-channel `deliveries`, and the post status `partly_sent`. The upgrade keeps every route and every waiting post. Going back to rc.1 needs the backup `update.bat` makes first: reversing the database change keeps only each account's first channel.
- **Settings are stored in the database** and apply at once. `.env` is only read, once, to import an earlier install.
- **The panel only accepts connections from this computer,** with protection against requests from other websites.

### Security
- App Passwords and webhook URLs are never shown in full in the panel, its API, logs or problem reports.
- Django 5.2 LTS. No known vulnerabilities in the shipped Python and npm packages at release.

[Unreleased]: https://github.com/qazwsx08ghj/bluesky-discord-alert-bot/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/qazwsx08ghj/bluesky-discord-alert-bot/releases/tag/v1.0.0
