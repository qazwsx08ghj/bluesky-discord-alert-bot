# Named Discord channels (step 4)

A channel is a `DiscordWebhook` row: a unique name plus a webhook URL (secret). Code: `discord_alerts/channels.py`. Tests: `discord_alerts/tests_channels.py` and `discord_alerts/tests_rollback.py`.

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

## API (all routes unchanged; fields added)

- `/api/webhooks/`:
  - `url` is write-only. Responses show `url_masked`, `account_count`, `last_delivery` and `updated_at`.
  - Edits can leave `url` out, or send the masked value back, to keep the saved URL.
  - Only Discord webhook URLs are accepted, and errors never repeat the value.
- `/api/accounts/`:
  - New `channel_id` (null = follow the default), plus read-only `channel {id, name, is_default}` and `uses_default_channel`.
  - The old `discord_webhook_url` field still works: a URL is matched to a channel or saved as a new one, `""` means follow the default, and the masked value means unchanged. Responses show only the masked URL.
- `/api/settings/`:
  - `discord_webhook_url` (masked) and `has_discord_webhook_url` describe the default channel. New: `default_channel {id, name}`.
  - Saving a URL there makes the channel with that URL the default, or creates "Default channel". The previous default channel is kept.

## Secrets

- **API responses:** URLs are masked everywhere.
- **Sender logs:** they no longer include exception text, which contains the URL. They log the HTTP status or the error type instead.
- **Safety net:** a logging filter (`discord_alerts/logfilter.py`) masks any webhook token in any console log line, including Django's, urllib3's and tracebacks.
- **Error pages:** `deliver_alert` hides `webhook_url` on Django error pages.
- **Private files:**
  - Nothing private is served. Django serves only static and media folders, and a startup check (`botconfig.E001`) refuses to start if one of them is, or contains, the project folder, the database, `.env` or `backups/`.
  - Tests request the private paths directly, with and without debug mode, and all of them return `404`.

## Rollback to step 3

The old columns are kept as exact copies after every channel change:
- `MonitoredAccount.discord_webhook_url` holds the account's channel URL, or `""`.
- `BotSettings.discord_webhook_url`, and with it `.env`, holds the default channel's URL.

Step 3 code routes by those, so after `git checkout <step 3 commit>` it sends every account to the same place. The new columns accept inserts from older code. `Step3RollbackTests` checks both by running the real step 3 code. To remove the step 4 schema as well, before checking out:

```
python manage.py migrate bluesky_monitor 0002
python manage.py migrate discord_alerts 0001
```

The backup taken before this step is in `backups/2026-10-21_before-step-4/`. These copies end on the same schedule as the `.env` copy: they are removed in step 10.
