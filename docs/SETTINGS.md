# Settings

The bot's settings live in one database row (`botconfig_botsettings`):
- the Bluesky handle;
- the App Password, which is write-only;
- the check interval.

Discord channels, including the default one, are separate rows (see [CHANNELS.md](CHANNELS.md)). Code: `botconfig/store.py`. Tests: `botconfig/tests.py`, `config/tests_step10.py`.

## Reading and saving

- **The database is the only place they are kept.** Every reader (the checker, the Bluesky login, Discord delivery and the API) asks `botconfig.store` each time, so a saved change applies without a restart: credentials from the next check, the interval within 30 seconds.
- **Saving** is one conditional UPDATE on a version number, so two saves at once can't lose either change; the later one retries on the newer version. If the database can't be written, nothing changes and the API answers `503 settings_not_saved`.
- **Nothing writes `.env`.** Going back to an older version means restoring a backup made before the update (`restore.bat`).

## The one-time import from an earlier install

The first time settings are needed and there is no row yet, they are imported once:
- **In Docker:** `setup.bat` mounts the earlier install's folder read-only and points `LEGACY_ENV_FILE` at its `.env`.
- **Without Docker:** the values Django loaded at start.

| `.env` key | Becomes |
|---|---|
| `BLUESKY_HANDLE`, `BLUESKY_PASSWORD` | The Bluesky login |
| `DISCORD_WEBHOOK_URL` | The default Discord channel ("Default channel") |
| `POLL_INTERVAL_MINUTES` | The interval; out of range is clamped to 1–60, unreadable becomes 5, each with a warning naming the key |

- The import happens once. A row is never overwritten from `.env` later; `python manage.py import_env_settings --force` is the explicit way to do that.
- Two processes starting at once end with one row: the slower one gets a unique-key error and uses the other's row.
- Logs name the keys found (`BLUESKY_PASSWORD=set`), never the values. The old `.env` file is only read.

## Protecting credentials

- **API:** the password is never returned, and webhook URLs are masked (`…/webhooks/<id>/****abcd`).
- **Logs and command output:** key names only, and a logging filter masks any webhook token.
- **Error pages:** they hide secret settings and variables.
- **Files:** the database is stored on this computer (inside Docker Desktop, or `db.sqlite3` without Docker), with the password in plain text, as `.env` held it before. `.env`, `*.sqlite3` and `backups/` are ignored by Git and never served by the web server.
- **Tests:** they never load the real `.env`; they use their own values and temporary databases.

## Backups before migrating

Every start runs `backup_db --if-pending` before `migrate`:
- in Docker, `prepare_data`;
- without Docker, `dev\start-without-docker.bat`.

When migrations are waiting, the database is copied with SQLite's backup API, the copy's integrity is checked, and the newest 5 such copies are kept. If the backup fails, the database is not migrated and the bot doesn't start.
