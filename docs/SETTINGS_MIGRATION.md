# Settings: from `.env` to the database (step 3)

Since step 3, the bot's settings live in the database table `botconfig_botsettings`, a single row. The settings are:
- Bluesky handle
- App Password
- default Discord webhook
- check interval

Code: `botconfig/store.py`. Tests: `botconfig/tests.py` and `botconfig/tests_rollback.py`.

## 1. Source of truth

- **The database is authoritative.** Every reader (the checker, the Bluesky login, Discord delivery and the API) asks `botconfig.store` each time. A saved change applies without a restart:
  - Credentials and webhook: from the next check or announcement.
  - Interval: within one heartbeat, at most 30 seconds.
- **`.env` is read once**, the first time settings are needed and no row exists. After that, `.env` is never read for these four settings. A hand edit is reported in the bot window and ignored.
- The explicit way to load `.env` values again is `python manage.py import_env_settings --force`.

## 2. The one-time import (idempotent)

| Situation | What happens |
|---|---|
| No settings row | The row is created from the environment (`.env`). Source is `env_import`, or `defaults` if `.env` was empty. Logged with key names only (`BLUESKY_PASSWORD=set`). |
| Row exists (every later start) | Nothing is imported. The saved settings are never overwritten. |
| Two processes start at once | One creates the row; the other gets a unique-key error and uses that row. |
| Interval in `.env` out of range or not a number | Clamped to 1–60, or set to 5, with a warning naming the key. |
| `import_env_settings` without `--force` | Does nothing if a row exists. |
| `import_env_settings --force` | Replaces the saved settings with `.env`'s values. A blank `.env` password keeps the saved one. |

## 3. Saving, and what happens when part of it fails

A save runs in this order:
1. **Validate.**
2. **Write the database.** One conditional UPDATE on the version number, so two saves at once can't lose either change; the second retries on the newer version.
3. **Copy to `.env`.**

The `.env` copy is written atomically: a temporary file in the same folder, then a single replace. Other lines (`SECRET_KEY`, comments) are kept. The copy carries `BOT_SETTINGS_VERSION=<n>`, the version it was written from.

| Failure | Database | `.env` | What the user sees | Recovery |
|---|---|---|---|---|
| Invalid input | unchanged | unchanged | `400 invalid_settings` with field errors | none needed |
| Database write fails | unchanged | unchanged | `503 settings_not_saved` | try again |
| `.env` write fails (locked, read-only, disk full) | **saved, in use** | old file, complete (never half-written) | `200`, with a `warning` and `env_file_in_sync: false` | repaired automatically on the next save or checker start |
| Process dies between the database commit and the `.env` write | saved, in use | old file, older version stamp | nothing | repaired on the next checker start |
| Another save lands during the `.env` write | both saved | rewritten until it matches the newest version | nothing | automatic |
| `.env` edited by hand | unchanged | edit kept, not applied | a warning in the bot window naming the keys | Settings page, or `import_env_settings --force` |

The rules behind this table:
- The two stores are never allowed to silently disagree.
- The database always wins.
- `.env` may lag behind, but only in a recorded way: `env_mirror_ok` and `env_mirror_error` on the row, and the version stamp in the file.
- A lagging copy is always repaired toward the database, never the other way round.

## 4. Protecting credentials

- **API:** the password is never returned, and the webhook URL is masked as `…/webhooks/<id>/****abcd`. Sending the masked value back means "unchanged". `test-discord` treats it as "use the saved URL".
- **Logs and command output:** key names and "set/empty" only, never values.
- **Error pages:**
  - The view and store functions mark their secret variables, so Django's error page hides them.
  - The settings row's text form never includes the password.
- **Files:**
  - `.env`, `.env.*` (including `.env.before-db-settings`, the copy of `.env` as it was before the first managed write), `*.sqlite3` and `backups/` are all ignored by Git.
  - The password is stored in the database in plain text, as it was in `.env`.
- **Tests:**
  - Tests never load the real `.env` values.
  - The `.env` copy is switched off during tests (`SETTINGS_ENV_MIRROR_PATH` is empty).
  - Tests that need a `.env` use a temporary file.
  - Subprocess tests switch it off explicitly.

## 5. Backups before migrating

`start.bat` and `setup.bat` run `python manage.py backup_db --if-pending` before `migrate`:
- When migrations are waiting, it copies the database to `backups/db-auto-<timestamp>.sqlite3` with SQLite's backup API and checks the copy's integrity.
- It keeps the newest 5 copies.
- If the backup fails, the database is not migrated and the bot doesn't start.

## 6. Rollback procedure

**Before rolling back**, make sure `.env` is current. Either:
- the Settings API shows `env_file_in_sync: true`, or
- run `python manage.py export_env_settings`.

**Code:**
```
git checkout main          # or the commit before step 3: 1a6689f
```
The old code reads `.env`, which holds the settings saved in the control panel. It ignores the extra `botconfig_botsettings` table and its migration record, and its `migrate --fake-initial` runs cleanly. This is tested by running the real step 2 code: `OldCodeRollbackTests`.

**Database:** usually nothing to do. To remove the table:
```
python manage.py migrate botconfig zero   # run with the step 3 code, before checking out the old code
```
To restore a full copy: stop the bot, then copy a file from `backups/` over `db.sqlite3`. The manual backup taken before this step is in `backups/2026-10-21_before-step-3/` (`db.sqlite3` and `.env`).

**Going forward again after a rollback:** the database still has the settings from before the rollback. If settings were changed with the old code (in `.env`), the bot window warns at start. Run `python manage.py import_env_settings --force` to load them.

## 7. End condition for the `.env` copy

The `.env` copy (`SETTINGS_ENV_MIRROR_PATH`, the version stamp, `export_env_settings`) exists **only** to support rolling back below step 3.

- **Step 8 (Docker):** the copy is off inside containers, because there is no writable `.env` there. Settings come only from the database volume. The first-run import from `env_file` stays.
- **Step 10 (final release and README rewrite):** the copy, the stamp and `export_env_settings` are removed. Rolling back below step 3 is then no longer supported. Restoring a `backups/` copy together with its matching code version remains the way back.
- The one-time import from `.env` and `import_env_settings` stay permanently, so existing installs and the Docker first run keep their setup.
