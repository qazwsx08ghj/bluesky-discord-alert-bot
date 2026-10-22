# The backend (Django)

A guide for developers. Setup and the rules for API changes are in [CONTRIBUTING.md](../CONTRIBUTING.md); the API itself is in [API.md](API.md) and, field by field, in [openapi.yaml](openapi.yaml).

## 1. Two processes, one database

The same code runs as two processes. In Docker, they are the two services in `docker-compose.yml`:

| Process | Command | Does |
|---|---|---|
| `web` | gunicorn (`config/wsgi.py`) | the API under `/api/`, and the built control panel (`config/frontend.py`) |
| `checker` | `manage.py run_scheduler` | checks Bluesky on a schedule, sends to Discord, runs Check All jobs |

They share one SQLite database (WAL mode, `bluesky_monitor/db.py`). Coordination goes through the database:
- **the scheduler lease** (`SchedulerState`): one checker at a time;
- **per-account locks** (`bluesky_monitor/locks.py`): a scheduled check and Check now never check the same account at once;
- **post claims:** every post is recorded once per account in `SeenPost`, unique on (account, post), before it is sent. A post is never announced twice, even across processes and restarts.

Without Docker (`dev\start-without-docker.bat`), `manage.py runserver` starts the checker inside the web process (`bluesky_monitor/apps.py`). Docker sets `SCHEDULER_IN_WEB=0`, so the web service never starts one.

## 2. Apps

| App | Main modules |
|---|---|
| `bluesky_monitor` | `models` (accounts, posts, scheduler state, jobs) · `bluesky` (the one shared login and session) · `poller` (read a feed, record new posts, deliver) · `scheduler` (the background checker, lease and heartbeat) · `check_all`, `jobs` (Check All as a background job) · `profiles` (profile lookup) · `account_status` (an account as the panel shows it) · `retention` |
| `discord_alerts` | `models` (channels = webhooks) · `channels` (routing, the default, safe deletion) · `sender` (sending, with retries) · `logfilter` (masks webhook URLs in logs) |
| `activity` | `log` (record events; never raises) · `problems` (each problem in plain words, with its fix) · `status` (`GET /api/status/`) · `retention` |
| `botconfig` | `store` (settings saved in the database) · `secrets` (masking) · commands: `prepare_data`, `backup_db`, `restore_db`, `import_env_settings`, `api_contract` |
| `config` | `settings` · `urls` · `security` (CSRF, Origin and `Sec-Fetch-Site` checks, JSON-only bodies, host names) · `frontend` (serving the panel) · `frontend_views` (settings, login test, status, profile lookup, check jobs) · `health` · `openapi` (the contract) |

## 3. The API contract

`config/openapi.py` describes every endpoint for [drf-spectacular](https://drf-spectacular.readthedocs.io/) (a development tool, in `requirements-dev.txt` only):
- **Views are not changed for the contract.** The descriptions are attached from outside, as `OpenApiViewExtension`s, so the running app doesn't import drf-spectacular. Its settings are applied only while generating.
- **Response shapes** are serializers written for documentation, next to the views they describe. Where a view returns a model serializer (accounts, channels, posts, activity), the contract uses that same serializer.
- **A post-processing step** adds:
  - the CSRF token requirement and the 403 and 415 answers on every change;
  - `invalid_json`;
  - OpenAPI 3.1 nullable types;
  - required fields on objects that always include all of them.

`config/tests_openapi.py` (tag `contract`) checks it:
- it calls every operation with real data in every state (account states, every login test outcome, each error);
- it validates the answers and requests against the contract, with JSON Schema 2020-12;
- objects are closed: an undocumented field fails;
- a coverage test fails if any operation has no successful answer checked.

## 4. Settings and secrets

- **Settings** (Bluesky login, check frequency) live in the database (`botconfig/store.py`) and apply at once.
- **`.env` is read once,** to import an earlier install's settings (`import_env_settings`), and never written.
- **The Django secret key** is generated into the data volume on first start (`SECRET_KEY_FILE`).
- **The App Password and webhook URLs** are never returned by the API:
  - answers show `has_bluesky_password` and masked URLs;
  - error pages hide them;
  - log lines are filtered (`discord_alerts/logfilter.py`).

## 5. Data safety

- **Start-up** (`prepare_data`): if migrations are waiting, back up first (`backup_db --if-pending`), then migrate.
- `backup.bat` / `restore.bat` call `backup_db` / `restore_db`:
  - a restore checks the backup;
  - it backs up the current data first.
- **History is capped:**
  - activity, by `ACTIVITY_RETENTION_DAYS`;
  - posts, at 1,000 finished posts per account;
  - posts still waiting to be sent are never removed.

## 6. Tests

Full reference, including how to run each suite: [TESTING.md](TESTING.md).

`python manage.py test` runs about 450 tests. Most live next to their app (`tests*.py`). Shared fakes:
- `bluesky_monitor/tests.py`: `FakeSession` and `feed_item`, a fake Bluesky;
- `discord_alerts`: delivery is patched per test;
- `config/testing/demo_server.py`: the whole app with fake Bluesky and Discord. It is used by the browser tests and the dev stack.

Every test uses temporary databases and fake credentials. Tests that start real processes blank the credential settings first, so the real `.env` is never used.
