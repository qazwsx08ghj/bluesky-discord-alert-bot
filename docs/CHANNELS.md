# Named Discord channels

A channel is a `DiscordWebhook` row: a unique name plus a webhook URL (secret). Code: `discord_alerts/channels.py`. Tests: `discord_alerts/tests_channels.py` and `config/tests_step10.py`.

## Routing

| Account's `channel` | Posts go to |
|---|---|
| a channel | that channel, always |
| empty (null) | the default channel; nothing is sent if there is no default |

- Changing the default moves only accounts that follow the default. An account that picked a channel keeps it, even if that channel used to be the default.
- At most one default channel exists. The database enforces this (`discordwebhook_single_default`), and the switch happens in one transaction.
- Each channel records its last delivery: time, ok, and a message without the URL.

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
  - The old `discord_webhook_url` field no longer exists; sending it is refused (`400`) with a pointer to `channel_id`.
- `/api/settings/`:
  - `default_channel {id, name}` names the default channel. Change it with `POST /api/webhooks/{id}/make_default/`.
  - There are no `discord_webhook_url` or `has_discord_webhook_url` fields any more.

## Secrets

- **API responses:** URLs are masked everywhere.
- **Sender logs:** they no longer include exception text, which contains the URL. They log the HTTP status or the error type instead.
- **Safety net:** a logging filter (`discord_alerts/logfilter.py`) masks any webhook token in any console log line, including Django's, urllib3's and tracebacks.
- **Error pages:** `deliver_alert` hides `webhook_url` on Django error pages.
- **Private files:**
  - Nothing private is served. Django serves only static and media folders, and a startup check (`botconfig.E001`) refuses to start if one of them is, or contains, the project folder, the database, `.env` or `backups/`.
  - Tests request the private paths directly, with and without debug mode, and all of them return `404`.

How existing installs moved to named channels, and the old columns: [history/MIGRATIONS.md](history/MIGRATIONS.md).
