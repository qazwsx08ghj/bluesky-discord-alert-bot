# Database migrations during development

> **Historical document.** How each development step's database changes carried existing installs forward, and how they could be undone at the time (October 2026, before the first release). The current behaviour of each feature is in its feature note ([CHANNELS.md](../CHANNELS.md), [SETTINGS.md](../SETTINGS.md), [ACTIVITY_AND_STATUS.md](../ACTIVITY_AND_STATUS.md), [ACCOUNTS_AND_CHECKS.md](../ACCOUNTS_AND_CHECKS.md)). Every start still backs the database up before migrating (SETTINGS.md, "Backups before migrating").

## Named Discord channels (step 4)

### Migration of existing setups (`bluesky_monitor` 0003, `discord_alerts` 0002)

Before step 4, routing was the account's own `discord_webhook_url`, else the saved default URL. The channel list was **not** used for routing. The migration reproduces that exactly:

1. The saved default URL becomes the default channel. That URL comes from `BotSettings`, or from `.env` when upgrading straight from step 2. An existing channel with the same URL is reused; otherwise a channel named "Default channel" is created.
2. Leftover "default" flags on other channels are cleared. Those flags had no effect before, so nothing that used to be sent changes. With no saved default URL, no channel is default, so followers still receive nothing.
3. Each account with its own URL gets the channel with that URL, created as "Channel for @handle" if there is none. Accounts that shared a URL now share one channel. An explicit URL stays explicit, even if it equals the default.
4. Reversible: undoing the migration leaves the old URL columns in place, and they already route correctly.

### The old columns (removed in step 10)

Until step 10, the pre-step-4 columns `MonitoredAccount.discord_webhook_url` and `BotSettings.discord_webhook_url` (and `.env`) were kept as copies of the channels, so step 3 code could run after a rollback. Step 10 removed them (`bluesky_monitor` 0007, `botconfig` 0002). Before a column is dropped, any URL in it that has no channel gets one, so no account loses its destination, and both migrations can be undone (they fill the columns from the channels again). `config/tests_step10.py` checks both directions.

## Settings in the database (steps 3 and 10)

Until step 10, every save was also copied into `.env` (with a status field, `env_file_in_sync`), so that older versions could run after a rollback. Step 10 removed that copy: settings live only in the database, and going back means restoring a backup.

## Activity log and status (step 5)

### Database changes (reversible)

- New table `activity_activityevent` (`activity` 0001).
- New nullable columns:
  - `MonitoredAccount.last_checked_at`, `last_check_ok` and `last_check_category`
  - `SchedulerState.last_cycle_summary`
  
  (`bluesky_monitor` 0004).
- **Going back:** since step 10, the older versions' compatibility copies are gone, so an older version can't run on this database. To go back, restore a backup made before the update (`restore.bat`) and run the matching version ([DOCKER.md](../DOCKER.md), "If an update goes wrong").

## Profile lookup, login test, Check All and account status (step 6)

### Database changes (reversible)

- **`bluesky_monitor` 0005, new nullable columns:**
  - `MonitoredAccount`: `announce_posts`, `announce_reposts`, `announce_replies`, `first_checked_at`, `first_check_posts`, `last_success_at`, `last_error_at`, `last_error_category`
  - `SeenPost`: `kind`
- Missing announce values mean the defaults, and posts without a kind are sent as plain posts.
- **Backfill:** existing accounts get the announce defaults. A working step 5 check becomes `last_success_at`, and a failing one becomes `last_error_*`.
- **Going back:** since step 10, the older versions' compatibility copies are gone, so an older version can't run on this database. To go back, restore a backup made before the update (`restore.bat`) and run the matching version ([DOCKER.md](../DOCKER.md), "If an update goes wrong").
