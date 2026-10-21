# Named Discord channels (step 4)

A channel is a `DiscordWebhook` row: a unique name plus a webhook URL (secret). Code: `discord_alerts/channels.py`. Tests: `discord_alerts/tests_channels.py` and `config/tests_step10.py`.

## Routing

| Account's `channel` | Posts go to |
|---|---|
| a channel | that channel, always |
| empty (null) | the default channel; nothing is sent if there is no default (as before) |

- Changing the default moves only accounts that follow the default. An account that picked a channel keeps it, even if that channel used to be the default.
- At most one default channel exists. The database enforces this (`discordwebhook_single_default`), and the switch happens in one transaction.
- Each channel records its last delivery: time, ok, and a message without the URL.

## Migration of existing setups (`bluesky_monitor` 0003, `discord_alerts` 0002)

Before step 4, routing was the account's own `discord_webhook_url`, else the saved default URL. The channel list was **not** used for routing. The migration reproduces that exactly:

1. The saved default URL becomes the default channel. That URL comes from `BotSettings`, or from `.env` when upgrading straight from step 2. An existing channel with the same URL is reused; otherwise a channel named "Default channel" is created.
2. Leftover "default" flags on other channels are cleared. Those flags had no effect before, so nothing that used to be sent changes. With no saved default URL, no channel is default, so followers still receive nothing.
3. Each account with its own URL gets the channel with that URL, created as "Channel for @handle" if there is none. Accounts that shared a URL now share one channel. An explicit URL stays explicit, even if it equals the default.
4. Reversible: undoing the migration leaves the old URL columns in place, and they already route correctly.

## Safe deletion

`DELETE /api/webhooks/{id}/`:

| Situation | Result |
|---|---|
| Not used and not the default | `204` |
| Used by accounts | `409 channel_in_use`, with the list of accounts. Nothing changes. |
| The default channel | `409 channel_is_default` |
| `?reassign_to=<id>` | The accounts move to that channel. If the deleted channel was the default, that channel becomes the default. |
| `?reassign_to=default` | The accounts follow the default. Refused (`409 no_default_channel`) if there is none, and not allowed for the default channel itself. |
| Reassigning to itself, or to an unknown channel | `400 invalid_reassign` |

The database also refuses (`PROTECT`), so a channel assigned at the same moment is never lost. `PATCH is_default=false` on the default is refused; use `POST /api/webhooks/{id}/make_default/` on another channel instead.

## API

- `/api/webhooks/`:
  - `url` is write-only. Responses show `url_masked`, `account_count`, `last_delivery` and `updated_at`.
  - Edits can leave `url` out, or send the masked value back, to keep the saved URL.
  - Only Discord webhook URLs are accepted, and errors never repeat the value.
- `/api/accounts/`:
  - `channel_id` (null = follow the default), plus read-only `channel {id, name, is_default}` and `uses_default_channel`.
  - The old `discord_webhook_url` field was removed in step 10; sending it is refused (`400`) with a pointer to `channel_id`.
- `/api/settings/`:
  - `default_channel {id, name}` names the default channel. Change it with `POST /api/webhooks/{id}/make_default/`.
  - The old `discord_webhook_url` and `has_discord_webhook_url` fields were removed in step 10.

## Secrets

- **API responses:** URLs are masked everywhere.
- **Sender logs:** they no longer include exception text, which contains the URL. They log the HTTP status or the error type instead.
- **Safety net:** a logging filter (`discord_alerts/logfilter.py`) masks any webhook token in any console log line, including Django's, urllib3's and tracebacks.
- **Error pages:** `deliver_alert` hides `webhook_url` on Django error pages.
- **Private files:**
  - Nothing private is served. Django serves only static and media folders, and a startup check (`botconfig.E001`) refuses to start if one of them is, or contains, the project folder, the database, `.env` or `backups/`.
  - Tests request the private paths directly, with and without debug mode, and all of them return `404`.

## The old columns (removed in step 10)

Until step 10, the pre-step-4 columns `MonitoredAccount.discord_webhook_url` and `BotSettings.discord_webhook_url` (and `.env`) were kept as copies of the channels, so step 3 code could run after a rollback. Step 10 removed them (`bluesky_monitor` 0007, `botconfig` 0002). Before a column is dropped, any URL in it that has no channel gets one, so no account loses its destination, and both migrations can be undone (they fill the columns from the channels again). `config/tests_step10.py` checks both directions.
