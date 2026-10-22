# Named Discord channels

A channel is a `DiscordWebhook` row: a unique name plus a webhook URL (secret). Code: `discord_alerts/channels.py` (routing, deletion), `discord_alerts/mentions.py` (role mentions), `bluesky_monitor/poller.py` (delivery). Tests: `discord_alerts/tests_channels.py`, `bluesky_monitor/tests_destinations.py`, `bluesky_monitor/tests_processes.py` and `config/tests_step10.py`.

## Routing

An account has 1 to 10 **destinations** (`AccountDestination`), each with an optional role to mention:

| Destination's `channel` | Posts go to |
|---|---|
| a channel | that channel, always |
| empty (null) | the default channel; nothing is sent there if there is no default |

- An account has each channel at most once, and at most one destination that follows the default (database constraints `destination_one_per_channel`, `destination_one_default`). A new account follows the default.
- If the default is also one of the account's own channels, the post is sent there once, with the account's own destination and role.
- Changing the default moves only destinations that follow the default.
- At most one default channel exists. The database enforces this (`discordwebhook_single_default`), and the switch happens in one transaction.
- Each channel records its last delivery: time, ok, and a message without the URL.

## Deliveries: one per post per channel

When a check notices a new post, it records the post (`SeenPost`) and, in the same transaction, one `PostDelivery` per channel the account sends to at that moment, with the role set at that moment.

- **No duplicates:** the post can only be recorded once per account (`seenpost_unique_per_account`), and each channel only once per post (`delivery_once_per_channel`). Several checks at once, in several processes, still send each post once per channel (`tests_processes.TwoChannelsAcrossProcessesTests`).
- **Independent retries:** each delivery has its own attempts, retry time (1, 5, 30 minutes, or longer if Discord asks), 24-hour limit and crash recovery. A failing channel holds up only its own later posts (so they keep their order); other channels go on and are never sent again.
- **The post's `status` sums up its deliveries:** `pending` while any is unsent, then `sent`, `partly_sent` (some channels got it, some gave up), `failed`, or `skipped` (every delivery was cancelled).
- **Old posts are never sent again.** Deliveries are only made when a post is first noticed, so adding a channel, changing a role or changing the default affects new posts only. Unsent deliveries keep the channel and role they were made with, also when the default changes.
- **Removing a channel from an account** cancels its unsent deliveries (`pending`, `retry` → `cancelled`). A delivery being sent at that moment (`sending`) is left alone: the request may already have reached Discord, so it can't be recalled. Its result is recorded when it finishes.

## Role mentions

- A destination's `role_id` is `""` (no mention) or a Discord Role ID: 17 to 20 digits. The API also accepts the `<@&ID>` form and stores the digits. `@everyone`, `@here` and anything else are refused with a message saying what a Role ID is and how to copy one.
- The notification then has the message text `<@&ROLE_ID>` above the embed, and `allowed_mentions: {"parse": [], "roles": ["ROLE_ID"]}`. Without a role there is no message text, and `allowed_mentions: {"parse": []}`. `parse` is always empty, so @everyone, @here, users and other roles are never pinged. The post's own text is only in the embed, where Discord never pings anyone.
- **What a webhook can and can't check:** a webhook can't read the server's roles, so the bot can't check that a role exists, that it can be mentioned (Discord's "Allow anyone to @mention this role"), or who is in it. It can check one dangerous mistake: Discord uses the server's own ID as the ID of its @everyone role. Before a channel's first mention, the bot reads the webhook (`GET` on the webhook URL; nothing is posted) to learn the server ID (`DiscordWebhook.guild_id`, cleared when the URL changes). A Role ID equal to it is refused in the form once the server ID is known, and is never mentioned in a notification: the notification is sent without the mention, and the activity log says why (`mention_not_sent`, category `mention_refused`). The same happens if Discord doesn't say which server it is. If Discord can't be asked, the delivery waits and is retried like a failed send.

## Safe deletion

`DELETE /api/webhooks/{id}/`:

| Situation | Result |
|---|---|
| Not used and not the default | `204` |
| Used by accounts | `409 channel_in_use`, with the list of accounts. Nothing changes. |
| The default channel | `409 channel_is_default` |
| `?reassign_to=<id>` | The accounts move to that channel. If the deleted channel was the default, that channel becomes the default. |
| `?reassign_to=default` | The accounts follow the default. Refused (`409 no_default_channel`) if there is none, and not allowed for the default channel itself. |

When the accounts move, an account that already sends to the new place keeps that destination and its role. Unsent deliveries move to the new channel, keeping their attempts and retry time, unless the post already has a delivery there; then they are cancelled, so nothing is sent twice. A delivery being sent is never moved, and a send only claims a delivery whose channel hasn't changed since it was read, so a moved delivery can't be sent to the old channel.
| Reassigning to itself, or to an unknown channel | `400 invalid_reassign` |

The database also refuses (`PROTECT`), so a channel assigned at the same moment is never lost. `PATCH is_default=false` on the default is refused; use `POST /api/webhooks/{id}/make_default/` on another channel instead.

## API

- `/api/webhooks/`:
  - `url` is write-only. Responses show `url_masked`, `account_count`, `last_delivery` and `updated_at`.
  - Edits can leave `url` out, or send the masked value back, to keep the saved URL.
  - Only Discord webhook URLs are accepted, and errors never repeat the value.
- `/api/accounts/`:
  - `destinations`: `[{channel_id, role_id}]` (`channel_id` null = follow the default; `role_id` `""` = no mention), plus read-only `channel {id, name, is_default}` and `follows_default` on each. Saving replaces the whole list; leaving it out keeps it.
  - Errors are per row: `{"destinations": [{}, {"role_id": ["…"]}]}`. List errors (`[]`, more than 10) are plain messages.
  - The old `discord_webhook_url` and `channel_id` fields no longer exist; sending either is refused (`400`) with a pointer to `destinations`.
- `/api/accounts/{id}/posts/`: each post has `deliveries: [{channel, role_id, status, attempts, next_attempt_at, sent_at}]`. Posts sent before the upgrade have none.
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

## Upgrading, and going back

Migrations `bluesky_monitor` 0008 and 0009 (with `discord_alerts` 0003) move existing data over without changing where anything goes:

- each account gets one destination, its own channel or "the default channel";
- each post still waiting to be sent gets one delivery to the channel it would have gone to, with its attempts, retry time and error, so it is sent once, not again;
- a waiting post with nowhere to go is marked `skipped`;
- posts already sent or given up get no deliveries; their status stays.

`update.bat` backs up the database before migrating. **Going back is not lossless** once accounts have several channels or roles: reversing the migrations keeps each account's first destination only, drops roles, and keeps one delivery's state per post. For a full rollback, restore the backup made before the upgrade (`restore.bat`, a `db-before-update-*` file).

How existing installs moved to named channels, and the old columns: [history/MIGRATIONS.md](history/MIGRATIONS.md).
