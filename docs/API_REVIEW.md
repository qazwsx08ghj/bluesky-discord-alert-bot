# API review for the new control panel

Date: 2026-10-21. Scope: the Django/DRF backend as it stands, measured against the approved control panel plan (`prototype/control-panel.html`) and the six extra requirements (first-check clarity, real scheduler health, activity log, named channels, data that survives Docker, no duplicate polling).

Rule for every change below: keep what works, extend in place, keep existing URL paths. Nothing is rewritten unless it is broken.

---

## 1. Problems in the current code

These need fixing whatever the frontend looks like. Each was checked against the code and, where possible, the database.

| # | Problem | Evidence | Effect |
|---|---|---|---|
| B1 | **No migration files exist for `bluesky_monitor` or `discord_alerts`.** `setup.bat` runs `makemigrations` with no app names, which does nothing for apps without a `migrations` folder. | `db.sqlite3` has no `monitoredaccount`, `seenpost` or `discordwebhook` tables; `makemigrations --dry-run` reports "No changes", while naming the apps creates both `0001_initial` files. | Every fresh install fails on the first "Add account" ("no such table"). |
| B2 | **The scheduler starts inside every Django process** (`BlueskyMonitorConfig.ready()`). `runserver` runs two processes (reloader plus server), gunicorn runs one per worker, and most management commands also start it. All of them share `DjangoJobStore`. | `apps.py`, `scheduler.py` | Duplicate polls, races on `SeenPost`, and a crash path (an uncaught `IntegrityError` in the second process). |
| B3 | **The bot logs in to Bluesky again for every account on every poll** (`_get_client()` inside `poll_account`). Bluesky limits session creation per account (roughly 30 per 5 minutes and 300 per day). | `poller.py` | With 5 accounts at 5 minutes, that is about 1,440 logins a day. The login gets rate-limited within hours and shows up as "Invalid identifier or password". |
| B4 | **Reposts are announced with a broken link.** `_post_url()` combines the watched account's handle with the reposted post's record key. | `poller.py` | The Discord link opens the wrong post or a "not found" page. |
| B5 | **Posts are marked as seen before Discord accepts them**, and send failures are only logged. | `poll_account` creates `SeenPost`, then ignores the result of `send_discord_alert` | A failed send loses that post permanently and silently. |
| B6 | **Settings are read only once, at import** (`settings.BLUESKY_*`, `POLL_INTERVAL_MINUTES`). The API writes `.env`. | `settings.py`, `frontend_views.py` | This is why the UI needs a restart step. |
| B7 | **The feed is read with `limit=10` and no paging.** | `poller.py` | More than 10 posts between two checks means the extras are never announced. |
| B8 | **API responses expose secrets.** `GET /api/settings/`, `GET /api/accounts/` and `GET /api/webhooks/` return full webhook URLs, which act as passwords. | serializers | Anything that can read the page can post into the owner's Discord. |
| B9 | **Any website the owner visits can change the bot's settings.** DRF accepts form-encoded POSTs, which browsers send cross-site without a CORS preflight, to `127.0.0.1:8000`. | default parsers, no auth | A malicious page could replace the webhook or credentials. |
| B10 | The account list nests every `SeenPost` for every account. | `MonitoredAccountSerializer.seen_posts` | Responses grow without limit. |
| B11 | `SeenPost.post_uri` is unique across all accounts. | `models.py` | If two watched accounts repost the same post, only the first is ever announced. |

Smaller items: "Check All Now" loops in the browser one account at a time; `setup.bat` forces creating an admin user nobody needs; the test message still says "from your DRF bot".

---

## 2. Requirement by requirement

### R1. Say clearly when the first check is done, even if nothing was sent

- New fields on `MonitoredAccount`: `first_checked_at`, `last_checked_at`, `last_check_ok`, `last_error`, `last_error_code`.
- The first successful check writes an activity event `first_check_done` with the number of posts it noted. Example message: "First check done. Noted 10 existing posts; none were sent to Discord. New posts from now on will be announced." A brand-new account with zero posts gets the same event with 0.
- The account API exposes a computed `state`: `waiting_first_check`, `checking`, `active`, `paused` or `error`, plus `first_check: {done, at, seeded_posts}`.
- `POST /api/accounts/{id}/poll_now/` keeps `new_posts_announced` and `was_first_poll`, and adds `first_check` and `state`.
- A failed first check (handle not found, login rejected) leaves the account in `error` with a plain-language `last_error`. It is never reported as done.

### R2. Running status reflects the scheduler, not just the API

- New singleton `SchedulerState`: `instance_id`, `started_at`, `heartbeat_at`, `cycle_started_at`, `cycle_finished_at`, `next_run_at`, `last_cycle_error`, `lease_expires_at`, `run_requested_at`.
- The scheduler process (see R6) writes a heartbeat every 30 seconds and timestamps every cycle.
- `GET /api/status/` computes the state on the server:
  - `running`: the heartbeat is under 90 seconds old and the last cycle started within `interval + 2 min`.
  - `stalled`: the heartbeat is fresh but cycles are overdue, or one cycle has run over 10 minutes.
  - `stopped`: the heartbeat is older than 90 seconds. The API is up but the checker is not.
  - `not_started`: no heartbeat has ever been written.
- Bluesky login health is reported separately (`ok`, `failed`, `not_configured`, `unknown`), so "the checker runs but Bluesky rejects the login" is its own state.
- The frontend's "Can't reach the bot" means the API itself didn't answer. **New UI state needed:** "The checker has stopped" (API up, scheduler down). The prototype gets one extra banner for it.

### R3. Activity log with pagination, filters and retention

- New model `ActivityEvent`: `created_at` (indexed), `type`, `level` (`success`, `info`, `warning`, `error`), `account` (FK, `SET_NULL`) plus `account_label` snapshot, `channel` (FK, `SET_NULL`) plus `channel_label` snapshot, `message`, `detail` (JSON: post URL and text, HTTP status, seeded count), `fix` (a code the UI turns into a link: `update_login`, `update_webhook`, `check_handle`).
- Types: `post_sent`, `send_failed`, `check_failed`, `login_failed`, `login_ok`, `first_check_done`, `account_added`, `account_removed`, `channel_test_sent`, `channel_test_failed`.
- `GET /api/activity/` uses cursor pagination (newest first, stable while new events arrive): `page_size` 50, maximum 200. Filters: `group=all|sent|problems`, `type=`, `account=`, `channel=`, `since=`, `before=`.
- Retention: events older than **30 days** are deleted, with a hard cap of **10,000** rows. A daily job in the scheduler does this, and it also runs at scheduler start. Both limits come from environment variables (`ACTIVITY_RETENTION_DAYS`, `ACTIVITY_MAX_EVENTS`).
- `SeenPost` retention (it's only the duplicate guard): keep the newest 100 per account plus anything from the last 30 days. That is safely above the feed window (R6), so pruning can never cause a re-announcement.
- The "Problems" count on the dashboard comes from **current state** (login failing, a channel whose last delivery failed, accounts in `error`), not from counting log rows. It goes back to 0 once things are fixed.

### R4. Named channels use the existing `DiscordWebhook` model

- `MonitoredAccount.channel = ForeignKey(DiscordWebhook, null=True, on_delete=PROTECT, related_name="accounts")`. `null` means "use the default channel", so changing the default moves every such account at once.
- Add delivery health to `DiscordWebhook`: `last_delivery_at`, `last_delivery_ok`, `last_delivery_error`.
- Make `is_default` changes atomic (`transaction.atomic` in `save()`), and add a `POST /api/webhooks/{id}/make_default/` action.
- Validate URLs: accept only `https://discord.com/api/webhooks/<id>/<token>` (also `discordapp.com`). On save, call Discord's `GET` on the webhook to confirm it exists, which posts nothing.
- Responses never include the full URL. They return `url_masked` (`discord.com/api/webhooks/1189…/••••`), `account_count`, `is_default` and `last_delivery`. `url` becomes write-only.
- Deleting a channel safely:
  - Still assigned to accounts: `409 {code: "channel_in_use", accounts: [...]}`. The UI asks where to move them and retries with `?reassign_to=<id>` or `?reassign_to=default`. `PROTECT` also blocks this at the database level.
  - The default channel: `409 {code: "channel_is_default"}`. Another channel must be made default first. The last remaining channel can't be deleted while any account is active.
- Existing per-account `discord_webhook_url` values: a data migration creates (or reuses, matched by URL) a `DiscordWebhook` for each, named "Channel for @handle", and links it. The old field is removed in a later migration. Because of B1, real installs have no account rows, so in practice this only matters for anyone who created the tables by hand.
- The `.env` value `DISCORD_WEBHOOK_URL` is imported once as the default channel (see R5).
- `POST /api/webhooks/{id}/test/` (already exists) is kept. It gets the corrected message text and updates delivery health plus the setup step "test message sent". `POST /api/settings/test-discord/` stays as is, for a URL typed in before it's saved.

### R5. Data survives restarts, rebuilds and updates

- All state lives in **one named Docker volume** (`bot-data`, mounted at `/data`): `db.sqlite3`, the generated `secret_key`, and `backups/`.
- A named volume, not a bind mount to a Windows folder: SQLite locking and WAL are unreliable on Windows bind mounts. Named volumes live inside Docker Desktop's Linux VM.
- **Settings move from `.env` into the database** (new singleton `BotSettings`: `bluesky_handle`, `bluesky_app_password` (write-only), `poll_interval_minutes` (1–60), cached `bluesky_session`, `login_status` / `login_checked_at` / `login_error`, `test_message_sent_at`). This also fixes B6: both processes read it on every cycle.
- First-run import: if `BotSettings` is empty and the container was given the legacy `.env` values (compose `env_file: .env`, `required: false`), the entrypoint copies handle, password, interval and default webhook into the database once. Existing users keep their setup.
- The entrypoint, on every start:
  1. Creates `/data/secret_key` if it's missing, then reuses it forever.
  2. If `migrate --check` shows pending migrations, copies the database to `/data/backups/db-<timestamp>.sqlite3` (keeps the last 5).
  3. Runs `migrate --noinput`.
- The scheduler container starts only after the web container is healthy, so migrations always run first and only once.
- Rebuilds and updates replace images only and never touch the volume. The `.bat` scripts never pass `down -v`. The README explains that the volume holds all data, and adds `backup.bat` (copies the database out of the volume to `backups\` in the project folder).
- `restart: unless-stopped` on both services, so the bot comes back after a reboot once Docker Desktop is running.
- SQLite settings: WAL journal mode and `busy_timeout` 20s, so the web and scheduler processes can write at the same time.

### R6. APScheduler in Docker, with no duplicate polls or notifications

**One scheduler process, in its own container.**
- Remove the scheduler start from `AppConfig.ready()`. Add the management command `python manage.py run_scheduler` (`BlockingScheduler`).
- `docker-compose.yml` runs it as a separate `scheduler` service from the same image as `web`. `web` runs gunicorn with no scheduler, so the number of web workers no longer matters.
- Jobs are defined in code and use `MemoryJobStore`, so no stale jobs come back from the database. `django_apscheduler` stays installed for now so its existing tables and migrations aren't disturbed; removing it is an optional cleanup later.
- The poll job runs with `max_instances=1` and `coalesce=True`, plus a misfire grace time, so a slow cycle never overlaps the next one.
- The heartbeat job (every 30 seconds) also re-reads `BotSettings` and reschedules the poll job when the interval changes. That gives "apply immediately" with no restart.

**A lease, in case two checkers start anyway** (two compose projects, or the old `start.bat` pointed at the same database):
- The scheduler takes `SchedulerState.lease_expires_at` with a conditional `UPDATE ... WHERE lease_expires_at < now OR instance_id = me` and renews it with every heartbeat.
- A second instance that can't get the lease runs no polls. It waits, and it shows in the logs and in `GET /api/status/`.

**A duplicate guard at the data level**, which holds even if two polls overlap:
- Claim before sending. Inside a transaction, insert `SeenPost(account, post_uri, status="sending")`; the unique constraint becomes `(account, post_uri)`, which also fixes B11. If the insert fails, someone else already has that post, so skip it. Otherwise send, then set `status` to `sent` or `failed`. This fixes B5.
- Retries for failed sends: up to 3 more tries on later cycles, only within 24 hours, logged as `send_failed` until one works. Sends use Discord's `?wait=true`, which confirms delivery, so timeouts rarely cause a double post.
- Per-account check lock: a conditional update on `check_lock_until` means "Check now" and the scheduled cycle never poll the same account at once. A second request gets `409 {code: "already_checking"}`, and the UI shows "Checking…".
- `POST /api/accounts/check_all/` (new) doesn't poll in the web process. It sets `SchedulerState.run_requested_at`, the scheduler starts a cycle within a few seconds, and the API returns `202`. A single executor means no duplicates.

**Bluesky session reuse** (fixes B3):
- One session per cycle, stored as a session string in `BotSettings` and shared by both processes.
- It logs in again only when the session is missing or expired.
- If the login itself is rejected, it records `login_failed`, skips the cycle and backs off. It never tries again in a loop.

**Feed reading** (fixes B4 and B7):
- Read with `limit=30`, paging further only until a post that's already seen shows up (maximum 100 posts per cycle).
- Build the link from `post.author.handle`.
- Classify each feed item as `post`, `repost` (`item.reason` is a repost) or `reply` (`record.reply` is set), and announce it only if that account allows the type (decision 1). Reposts are titled "X reposted", with the original author shown. Skipped items are still recorded as `SeenPost(status="skipped")`, so turning a type on later never floods the channel with old items.

---

## 3. What the control panel needs from the API

| Method and path | Status | Purpose |
|---|---|---|
| `GET /api/status/` | **new** | Scheduler state, Bluesky login state, next/last check, counts (accounts, active, posts sent today, open problems), setup checklist `{bluesky_login, default_channel, test_message_sent, has_accounts, complete}`. The UI polls it every 15s. |
| `GET /api/accounts/` | change | Drop nested `seen_posts`; add `state`, `channel {id, name}`, `first_check`, `last_checked_at`, `last_post {text, url, sent_at}`, `last_error`, `avatar_url`, `did`. Allow `?page_size=` (maximum 100). |
| `POST /api/accounts/` | change | Takes `handle`, `display_name`, `channel_id` (null means default), `announce_posts` / `announce_reposts` / `announce_replies` (defaults true / true / false). Resolves the handle to a DID and avatar, and rejects unknown or duplicate handles with codes `handle_not_found` / `already_watched`. |
| `PATCH /api/accounts/{id}/` | keep, extended | Edit display name, channel, active flag, and the three announce options. The handle is read-only. |
| `DELETE /api/accounts/{id}/` | keep | Writes an `account_removed` event. |
| `POST /api/accounts/{id}/poll_now/` | keep, extended | Adds `state`, `first_check`, and `409 already_checking`. |
| `GET /api/accounts/{id}/posts/` | **new** | Paginated history of noted and sent posts for one account (replaces the nested list). |
| `POST /api/accounts/check_all/` | **new** | Asks the scheduler for an immediate cycle (`202`). |
| `GET /api/bluesky/profile/?handle=` | **new** | Lookup for the Add panel: `{did, handle, display_name, avatar_url, already_watched}`; `404 handle_not_found`. Uses the shared session. |
| `GET /api/settings/` | keep, extended | Same fields, plus `login_status`, `login_checked_at`. The webhook URL is replaced by `default_channel {id, name}`. |
| `PATCH /api/settings/` (`POST` still accepted) | change | Writes `BotSettings` in the database, not `.env`. Validates the interval (1–60). Saving a new password clears the cached session. |
| `POST /api/settings/test-login/` | **new** | Tries the given or saved credentials and records `login_status`. Saves nothing else. |
| `POST /api/settings/test-discord/` | keep | Test an unsaved URL. |
| `GET/POST /api/webhooks/` | change | Masked URLs, `account_count`, `last_delivery`, URL validation. |
| `PATCH/DELETE /api/webhooks/{id}/` | change | Safe delete rules from R4 (`409` with accounts, `?reassign_to=`). |
| `POST /api/webhooks/{id}/make_default/` | **new** | Atomic default switch. |
| `POST /api/webhooks/{id}/test/` | keep | Corrected copy; updates delivery health and the setup step. |
| `GET /api/activity/` | **new** | Cursor-paginated, filterable log (R3). |
| `/admin/` | remove | Unused; drops the forced `createsuperuser` step. |

Every error uses one shape: `{"code": "handle_not_found", "message": "...", "fields": {...}}`. The React app maps each `code` to its own friendly text and fix link, so wording stays in the frontend.

The old `templates/index.html` and `index` view are kept until the React app replaces them at `/`. Nothing breaks halfway through the migration.

---

## 4. Security changes (small, local-only scope)

- **Fixes B9**: unsafe API methods accept JSON only and need Django's CSRF token, plus Origin and fetch-metadata checks. (Step 7 replaced the planned `X-Requested-With: AlertBot` header with these, so no single header is relied on; see `docs/API.md` section 2.)
- `ALLOWED_HOSTS = localhost,127.0.0.1` stays strict. That blocks DNS-rebinding attacks.
- Compose publishes `127.0.0.1:8000:8000` only, so nothing is reachable from the network.
- Docker uses `DEBUG=False` and a per-install generated `SECRET_KEY`.
- Secrets (app password, webhook tokens) are write-only in every response. They are stored in plain text in the database, as they are in `.env` today. Encrypting them at rest would add a key to manage for little gain on a single-user local machine; it can be added later.

---

## 5. Docker layout (for the migration step)

- **One image, multi-stage:**
  1. Node: builds `frontend/` (React + Vite).
  2. Python: builds dependency wheels.
  3. Final `python:3.12-slim`: installs the wheels, copies `backend/` and the built frontend.
- Django serves the built React files with WhiteNoise, with the SPA fallback at `/`; the API stays under `/api/`. Same origin, so no CORS. React and Django stay separate codebases that talk only over REST, which a React Native app can reuse.
- **Services:**
  - `web`: gunicorn, 2 workers, with a healthcheck on `/api/health/`.
  - `scheduler`: `run_scheduler`, with a healthcheck that reads the heartbeat.
  - Both use the `bot-data` volume and `restart: unless-stopped`.
- **Scripts:**
  - `start.bat`: checks Docker Desktop is running, runs `docker compose up -d --build`, waits for health, opens `http://127.0.0.1:8000`.
  - `stop.bat`: `docker compose stop`.
  - `backup.bat`: copies the database out of the volume.
  - `setup.bat` and `venv` are retired.
- **Repository layout:** `backend/` (the current Django project, moved without code changes), `frontend/`, `docker/` (entrypoint), `docker-compose.yml`, `Dockerfile`.

---

## 6. Order of work (each step leaves the bot working)

1. **Commit initial migrations** for both apps, matching the current models exactly (fixes B1). Add tests for the poller and the API.
2. **Scheduler out of the web process**: `run_scheduler`, the lease, heartbeat, session reuse, claim-before-send, `(account, post_uri)` uniqueness, feed paging, repost link (B2–B5, B7, B11).
3. **Settings in the database**: `BotSettings`, the one-time `.env` import, settings applied immediately (B6).
4. **Channels**: the FK, data migration, safe delete, masked URLs (R4, B8).
5. **Activity log and status**: `ActivityEvent`, retention, `/api/activity/`, `/api/status/` (R1–R3).
6. **New endpoints**: profile lookup, test login, check all; account serializer changes (B10).
7. **Security hardening**: JSON-only, CSRF token with Origin and fetch-metadata checks, `/admin/` removed (B9). Check All moved to a background job.
8. **Docker**: Dockerfile, compose, entrypoint with backup-before-migrate, `.bat` scripts. Then test on Docker Desktop: restart, rebuild, update with existing data.
9. **React control panel**, built against the endpoints above, matching the prototype plus the "checker stopped" state.
10. **End-to-end test on Windows and README rewrite.**

---

## 7. Decisions (confirmed 2026-10-21)

1. **What gets announced is set per account.** New `MonitoredAccount` fields: `announce_posts` (default on), `announce_reposts` (default on), `announce_replies` (default off). They are set in the Add and Edit account panel. Turning a type on later only affects new items. Existing accounts get the defaults through the migration.
2. **Activity retention:** 30 days, capped at 10,000 events.
3. **Failed sends** retry automatically: up to 3 more tries on later checks within 24 hours, each one logged.

---

## 8. Implementation status

- **Step 1 (done):** initial migrations committed; `setup.bat`/`start.bat` apply them with `--fake-initial`.
- **Step 2 (done):** see `bluesky_monitor/locks.py`, `bluesky.py`, `poller.py`, `scheduler.py`, migration `0002`.
  - Checker lease and per-account lock are conditional database UPDATEs with expiry, so they hold across processes and recover from crashes.
  - `runserver` starts the checker only in its serving process; `manage.py run_scheduler` runs it standalone; a second checker stays on standby.
  - Posts are claimed before sending (unique per account) and move through `pending → sending → sent`, or `retry` (after 1, 5 and 30 min) and then `failed` after 4 attempts or 24 hours. A send interrupted by a crash is retried after 2 minutes.
  - **Known limit:** if a process dies after Discord stored a message but before it is marked sent, that one post is sent a second time after the 2-minute stale timeout. Discord webhooks have no idempotency key, so this narrow window can't be closed completely; `?wait=true` keeps it as small as possible.
  - One shared Bluesky session per cycle, saved for every process; refreshes are saved; expired sessions log in again once; a rejected password backs off for 10 to 60 minutes.
  - Existing `SeenPost` rows are backfilled as `sent`, so nothing old is announced again.
- **Step 3 (done):** see `botconfig/` and `docs/SETTINGS_MIGRATION.md`.
  - `BotSettings` (one row) is the source of truth; every reader asks `botconfig.store`, so changes apply without a restart (interval within 30 s via the heartbeat).
  - Imported once from `.env`; never overwritten afterwards (`import_env_settings --force` is the explicit override).
  - Saves: validate, then one conditional database UPDATE, then an atomic `.env` copy stamped with the settings version. A failed copy is recorded, reported, and repaired on the next save or start.
  - `GET /api/settings/` keeps its fields, masks the webhook URL, and adds `has_discord_webhook_url`, `settings_version`, `updated_at`, `env_file_in_sync`. `PATCH` is accepted. Errors use `{code, message, fields}`.
  - `start.bat` / `setup.bat` back up the database before pending migrations (`backup_db --if-pending`).
  - The `.env` copy is temporary: off in Docker (step 8), removed in step 10.
- **Step 4 (done):** see `discord_alerts/channels.py` and `docs/CHANNELS.md`.
  - `MonitoredAccount.channel` (FK, `PROTECT`, null = follow the default); one default channel, enforced by the database.
  - The migration reproduces the old routing exactly (account URL, else the saved default URL) and is reversible.
  - Safe delete with `409 channel_in_use` / `channel_is_default` and `?reassign_to=`; `make_default` action; delivery health per channel.
  - URLs are masked in every response; edits keep the saved URL unless a new one is sent; the sender no longer logs URLs, and a log filter masks any that slip through.
  - A startup check stops private files (database, `.env`, backups) from ever being in a served folder.
  - Old URL columns are kept as copies for rollback to step 3 until step 10.
  - Not done here (planned): checking a new URL against Discord when it is saved.
- **Step 5 (done):** see `activity/` and `docs/ACTIVITY_AND_STATUS.md`.
  - `GET /api/status/` decides `unavailable` / `not_configured` / `problem` / `running` from the checker's own heartbeat, check times and check results; a stopped or stuck checker is reported even though the API answers.
  - Every failure gets a plain-language category with a fix hint; the technical cause is kept separately (single-event view only).
  - `GET /api/activity/`: cursor-paged, newest first, filters `group`, `type`, `level`, `account`, `channel`, `since`, `before`. Sends, every retry attempt, give-ups, failed checks, first-check results, login problems and checker starts are logged; repeated problems are counted on one entry per hour.
  - Retention: 30 days or 10,000 entries, whichever comes first; only log rows are deleted, never the delivery queue.
  - Secrets are scrubbed before anything is stored; the console log filter uses the same patterns.
  - Not done here (planned for step 6): per-account `state` / `first_check` in the account API, `test-login`, `check_all`. `SeenPost` retention is not implemented yet.
- **Step 6 (done):** see `docs/ACCOUNTS_AND_CHECKS.md`.
  - `GET /api/bluesky/profile/?handle=`: uses Bluesky's public API (no login), cleans up handles and links, and answers found / not_found / unavailable / invalid / rate_limited / error. Answers are cached for 60 s, and the query is echoed for stale-answer handling. **Changed from the plan:** "not found" is a `200` with `result: not_found` (not a `404`), and it doesn't use the shared session.
  - `POST /api/settings/test-login/`: a throwaway sign-in that saves nothing (5 tests per 5 minutes). A working test of the saved login ends a back-off. `GET /api/settings/` adds `bluesky_login`.
  - `POST /api/accounts/check_all/`: **changed from the plan,** it runs in the request through the scheduler's own `check_accounts()` (per-account locks, claims) and returns per-account results, instead of a `202` with a queued run.
  - One account's unexpected error no longer stops a scheduled check.
  - Account API: `state`, `first_check`, `last_success_at`, `last_error` and the announce options. Handles are normalized, with `already_watched`. Reposts and replies are classified and announced per account.
  - Not done here: the planned `GET /api/accounts/{id}/posts/` and dropping the nested `seen_posts` (left for the React step); storing `did`/`avatar_url` on accounts; `SeenPost` retention.
- **Step 7 (done):** see `docs/API.md` (the full API reference, including security and error codes) and `config/security.py`.
  - **Check All is a background job,** as planned originally: `202` with a job, `GET /api/check-jobs/{id}/` (progress and per-account rows), `/latest/` (shown again after a refresh). The checker runs jobs in a thread, claimed under the scheduler lease, with the same account locks and post claims. Stopped checkers leave jobs `interrupted`; unclaimed jobs `expire` after 2 minutes. No Celery or Redis. Migration `bluesky_monitor` 0006 (a new table only).
  - **Cross-site protection without a custom header:** a CSRF token on every change, also for DRF views; Origin and `Sec-Fetch-Site` checks; JSON-only bodies, checked before anything reads them; no CORS. Host names are limited to localhost (DNS rebinding), and `start.bat` binds `127.0.0.1:8000`.
  - Uniform JSON errors (`code`, `message`, `detail` kept), JSON 404/500 under `/api/`, security headers, and error pages that hide webhook settings.
  - `/admin/`, DRF's browsable HTML pages and the `createsuperuser` setup step are removed. The admin's database tables are left in place for rollback.
  - The old page sends the CSRF token. `DEBUG` defaults to off.
  - **Test fix:** the rollback tests' processes could load the real `.env` and sign in to Bluesky with it. They now blank those keys, and a test checks it.
- **Step 8 (done):** see `docs/DOCKER.md`.
  - `Dockerfile` (two stages, exact versions from `requirements-lock.txt`, runs as user `bot`, no secrets inside) and `docker-compose.yml`: `web` (gunicorn) and `checker` (`run_scheduler`), both `restart: unless-stopped`, with health checks and one named volume, `alertbot_bot-data`, holding the database, backups, the generated secret key and the cache.
  - **Changed from the plan:** no `backend/` folder move (it wasn't needed); Docker Compose files live at the root. `setup.bat` stays, now for Docker, and imports an earlier venv install. The venv scripts moved to `dev\`.
  - Start-up: `prepare_data` (key once, backup if migrations wait, migrate, load settings) and `wait_for_database` for the checker. `GET /api/health/` for Docker and the scripts. A checker watchdog exits on a stalled heartbeat so Docker restarts it.
  - Scripts: `setup.bat`, `start.bat`, `update.bat` (backup, then pull or build, then recreate and check), `stop.bat`, `backup.bat`, sharing `docker\scripts\common.bat`. They check Docker Desktop (installed, running, recent, Linux containers), start it if needed, detect a busy port, wait for health, open the browser, and save a problem report. They never remove volumes.
  - Releases: `.github/workflows/release.yml` publishes multi-arch images to ghcr.io and a small download (`docker/make_release_bundle.py`) whose scripts pull instead of build.
  - Tests: the real `.bat` files under cmd.exe with a fake docker (`config/tests_deploy.py`); opt-in real-container tests (`config/tests_docker.py`). Also checked by hand: recovery after a full Docker Desktop restart, and restoring a backup.
- **Step 9 (done):** see `docs/FRONTEND.md`.
  - The React control panel in `frontend/` (Vite, TypeScript, React Router, TanStack Query), from the approved prototype. Screens: Setup (four steps), Accounts (summary, table, Check All, recent activity), Activity, Settings (login, channels, frequency, appearance, about), plus the bot status dialog.
  - **No API changes.** The panel uses the step 7 API as documented, including the CSRF token, the background Check All jobs (`GET /api/check-jobs/?limit=1` on load, so a running job shows again after a refresh) and the masked webhook URLs.
  - Django serves it (`config/frontend.py`): one page for the panel's addresses with a strict Content Security Policy, and the built files from `frontend/dist/assets` only. The previous page stays at `/classic/` and is shown if the panel isn't built.
  - Docker: a `panel` build stage (Node.js 22, `npm ci --ignore-scripts`) builds it; the image that runs has no Node.js.
  - Tests: 50 frontend integration tests (fake API), 14 browser tests against the real app with fake Bluesky and Discord (`config/testing/demo_server.py`), Django tests for serving and file safety, and `@docker` browser checks in the real-container tests.
  - Not done here: storing `did`/`avatar_url` on accounts (the table shows initials; the add drawer shows the real avatar), the paged posts endpoint, and removing `/classic/` (planned with the other step 10 clean-ups).
