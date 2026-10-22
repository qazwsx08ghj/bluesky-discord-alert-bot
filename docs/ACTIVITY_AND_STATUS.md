# Activity log and status

Code: `activity/` (log, problem categories, retention, status, API). The checker writes its health in `bluesky_monitor/scheduler.py` and `poller.py`. Tests: `activity/tests.py`.

## `GET /api/status/`

The status comes from what the checker itself writes to the database, never from the web server answering. So "the control panel works but the checker is down" is reported as such.

| `state` | When | What the user sees |
|---|---|---|
| `unavailable` | The checker never started, its heartbeat is older than 90 s, a check has run for more than 10 min, or a planned check is more than 2 min late | "The checker isn't running", with restart steps |
| `not_configured` | The checker runs, but there is no Bluesky login, no Discord channel, or no account | "Setup isn't finished", listing what's missing |
| `problem` | Running, but something is failing now: login rejected, an account can't be checked, a channel's last delivery failed, followers with no default channel, posts waiting to be retried, or the last check crashed | "Running, with problems", listing them |
| `running` | Everything above is fine | "Running normally" |

The states are checked in this order, so a stopped checker is reported even if setup is also unfinished.

The response contains:
- **`scheduler`:**
  - `state` (`running`, `checking`, `stalled`, `stopped` or `not_started`), `heartbeat_at`, `heartbeat_age_seconds` and `active_since`
  - `checking_now`, `check_interval_minutes` and `next_check_at`
  - `last_check`: `{finished_at, result, message, problem, accounts_checked, accounts_failed, posts_sent}`
- **`bluesky_login`:** `ok`, `failed`, `rate_limited`, `not_configured`, or `unknown` (not tried yet with the current login).
- **`setup`:** `{bluesky_login, discord_channel, accounts, complete, missing[]}`.
- **`counts`:**
  - accounts in total and active, and channels
  - `pending_deliveries`, `deliveries_retrying`
  - `sent_last_24h`, `failed_sends_last_24h`
- **`problems`:** the current problems, errors first. They come from the current state, not from counting log entries, so each one disappears once it's fixed.
- **`recent_errors`:** the last 5 warnings or errors from the log in the past 24 hours.

The limits are configurable: `SCHEDULER_LEASE_SECONDS` (90), `SCHEDULER_STALL_MINUTES` (10), `SCHEDULER_OVERDUE_GRACE_SECONDS` (120).

## Problem categories (`activity/problems.py`)

Every failure has one category code with a plain-language `title`, an `explanation` that says what to do, and a `fix` hint for the control panel (`update_login`, `update_webhook`, `choose_channel`, `check_handle`, `restart_bot`, `wait`, `none`).

| Area | Categories |
|---|---|
| Bluesky | `bluesky_not_configured`, `bluesky_login_rejected`, `bluesky_rate_limited`, `bluesky_unavailable`, `account_not_found`, `bluesky_error` |
| Discord | `discord_webhook_invalid` (HTTP 401/403/404), `discord_rate_limited` (429), `discord_unavailable` (network errors, 5xx), `discord_error`, `discord_test_failed`, `send_expired`, `no_channel`, `mention_refused` (sent without the role mention: the Role ID is the server's own ID, or the server couldn't be confirmed) |
| Checker | `scheduler_not_started`, `scheduler_stopped`, `scheduler_stalled`, `internal` |

The technical cause (exception type and message, HTTP status, Discord's reply) is stored separately in `technical`. It is shown only by `GET /api/activity/{id}/`, for troubleshooting.

## The activity log: `GET /api/activity/`

- **Order and paging:** newest first. Paging uses a cursor, so pages stay stable while new events arrive. `page_size` is 50 by default and at most 200; follow the `next` and `previous` links.
- **Filters,** combined with AND:
  - `group=all|sent|failed_checks|failed_sends|retries|problems`
  - `type=` and `level=` (each takes a comma-separated list)
  - `account=<id>`, `channel=<id>`
  - `since=` and `before=`, each a date or a time
- **Invalid filters:** `400 {code: "invalid_filter", message, fields}`.
- **Each entry** has:
  - `type`, `level` (`success`, `info`, `warning`, `error`) and `message` (plain language)
  - `problem` (the category explained), and `account` / `channel` (`{id, label}`)
  - `attempt` and `is_retry` (the send attempt is greater than 1)
  - `detail` (post link and text, next retry time, number of posts noted)
  - `repeat_count` and `last_seen_at`
- **The log is read-only.**

Event types:
- **Sending** (one entry per channel): `post_sent`, `send_failed` (will retry), `send_gave_up`, `post_not_sent` (no channel), `mention_not_sent` (sent, but without the role mention).
- **Checking:** `check_failed`, `check_recovered`, `first_check_done` (with the number of posts noted).
- **Login and checker:** `login_failed`, `login_recovered`, `cycle_failed`, `checker_started`, `checker_stopped`.
- **Changes from the panel:** `account_added`, `account_removed`, `channel_test_sent`, `channel_test_failed`.

**Repeated problems:** the same problem for the same account or channel within an hour is counted on one entry (`repeat_count`) instead of adding new lines. A login that fails every minute therefore adds at most 24 entries a day. Sends and retries are never merged, so each attempt is visible.

## Separate from the delivery queue

- Posts waiting to be sent live in `SeenPost`. The log lives in `ActivityEvent`, and nothing in the log points to a queued post.
- The log refers to accounts and channels by id plus a name, without a foreign key. Deleting an account or channel is never blocked by its log entries, the entries keep the name.
- Writing to the log never raises an error. If the database refuses the entry, the check or send still completes.

## Retention

- Entries older than **30 days** are deleted, and the log never holds more than **10,000** entries (the newest are kept). Whichever limit is reached first applies.
- The limits are `ACTIVITY_RETENTION_DAYS` and `ACTIVITY_MAX_EVENTS`.
- Cleanup runs when a checker starts and after every check, which is at least once an hour. It can also be run with `python manage.py prune_activity`.
- Cleanup deletes only `ActivityEvent` rows. Pending, sending and retrying posts are in another table and are never touched; a test checks this. A failed cleanup never stops the checker.

## Secrets

- **Scrubbing before storing:** every message, technical note and detail value is passed through `botconfig.secrets.scrub` before it's stored. It removes:
  - Discord webhook tokens, Bluesky session tokens (JWTs), and bearer tokens
  - `password=` and `token:` style values, and App Password-shaped strings
  - the exact saved App Password, Bluesky session and `SECRET_KEY`
- **Console log:** the same patterns, except the exact saved values, now also apply to the console log filter.
- **Discord failures** keep the HTTP status and Discord's reply, scrubbed. Network failures keep only the error type, because the error text contains the URL.
- **Failed feed reads** show only the error type in the console log. The scrubbed detail is in the activity entry.
- **Tests** run a failed send, a failed check and a failed login with real-looking secrets, then search every stored entry and every `/api/activity/` and `/api/status/` response for them.

The database changes that introduced the activity log: [history/MIGRATIONS.md](history/MIGRATIONS.md).
