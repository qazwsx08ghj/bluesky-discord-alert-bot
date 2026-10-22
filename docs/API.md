# Control panel API

Reference for the React control panel and any other client, such as a future React Native app. It describes the API as it is now; how it changed during development is in [history/API_CHANGES_BEFORE_1.0.md](history/API_CHANGES_BEFORE_1.0.md).

**The exact shapes are in [openapi.yaml](openapi.yaml):** an OpenAPI 3.1 contract generated from the code (`python manage.py api_contract --write`). It lists every field, type, value and error, and tests check every answer against it. This page explains the API in words. Where the two differ, the contract is right; please report it. The panel's TypeScript types are generated from the contract. How to change the API: [CONTRIBUTING.md](../CONTRIBUTING.md), section 4.

Topic pages with more background:
- [CHANNELS.md](CHANNELS.md): channels
- [ACTIVITY_AND_STATUS.md](ACTIVITY_AND_STATUS.md): activity and status
- [ACCOUNTS_AND_CHECKS.md](ACCOUNTS_AND_CHECKS.md): lookup, login test, checks
- [SETTINGS.md](SETTINGS.md): settings

## 1. Basics

| | |
|---|---|
| Address | `http://127.0.0.1:8000/api/…` (or `http://localhost:8000`). The panel itself is at `/`. |
| Format | JSON in, JSON out. Times are ISO 8601 in UTC, e.g. `2026-10-21T14:03:00Z`. |
| Login | None. The panel is reachable only from the computer it runs on (see section 2). |
| Changes | `POST`, `PUT`, `PATCH` and `DELETE` need the CSRF token (section 2). `GET` never changes anything. |
| Caching | Every `/api/` response has `Cache-Control: no-store`. |

**Paging:**
- Lists of accounts and channels use page numbers: `{count, next, previous, results}`, 20 per page, `?page=2`.
- The activity log uses a cursor: `{next, previous, results}`.
- Check jobs: `{results}`.

**Secrets are write-only:**
- The Bluesky App Password and Discord webhook URLs are accepted in requests but never returned.
- Responses show `has_bluesky_password`, or a masked URL such as `https://discord.com/api/webhooks/1234/****`.
- Sending the masked value back means "keep the saved one".

## 2. Security

The panel has no login, so it is protected by where it can be reached from and by how requests must be made. **Every layer is independent; a custom header alone is never trusted.**

| Layer | What it stops | How |
|---|---|---|
| Listens on this computer only | Other devices on the network | In Docker (`start.bat`), the port is published as `127.0.0.1:8000:8000`. Without Docker, `dev\start-without-docker.bat` runs `runserver 127.0.0.1:8000`. |
| Local host names only | DNS rebinding (a web page reaching 127.0.0.1 under its own domain name) | `ALLOWED_HOSTS=localhost,127.0.0.1,[::1]`. Any other `Host` gets `400`. Adding another name triggers a startup warning (`alertbot.W001`). |
| Fetch metadata | Requests from other websites | A change with `Sec-Fetch-Site: cross-site` gets `403 cross_site_request`. `same-site` (e.g. another port) is refused unless its origin is in `CSRF_TRUSTED_ORIGINS`. |
| Origin check | Requests from other websites, including `Origin: null` | When `Origin` is sent, it must be the panel's own origin or in `CSRF_TRUSTED_ORIGINS`. Otherwise `403 cross_site_request`. |
| CSRF token | Requests from any page that can't read the panel's cookie | Every change must send `X-CSRFToken` matching the `csrftoken` cookie (Django's check). DRF normally exempts its views; `config.security.RequestGuardMiddleware` applies the check to every view. Otherwise `403 csrf_failed`. |
| JSON only | HTML forms, which browsers send cross-site without asking | A change with a body that isn't `application/json` gets `415 json_required`, before anything reads the body. A script that sends JSON to another site needs CORS permission, which this server never gives. |
| No CORS | Other websites reading the bot's data | No `Access-Control-Allow-*` headers are ever sent, so browsers block cross-origin reads and preflights. |
| Response headers | Framing, type sniffing, caching | `X-Frame-Options: DENY`, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` (on `/api/`), `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Cache-Control: no-store`. |
| No admin, no HTML API pages | Extra attack surface | `/admin/` was removed. DRF's browsable HTML pages and form parsers are off; API addresses always return JSON. |
| Secrets stay hidden | Leaks through responses, logs and error pages | Masked or write-only in responses. A log filter masks webhook tokens. Error pages and reports also hide settings named `*WEBHOOK*`, `*TOKEN*`, `*PASS*`, `*KEY*` and `*SECRET*`. Unexpected errors return `500 {code: "internal"}` with no details. |

**What a client must do:**
1. Get the token:
   - **Browser:** the panel page (`GET /`) sets the `csrftoken` cookie (`SameSite=Strict`, readable by JavaScript). Read it from `document.cookie`, or call `GET /api/csrf/` once.
   - **Other apps:** `GET /api/csrf/` returns `{"csrf_token", "header_name": "X-CSRFToken", "cookie_name": "csrftoken"}` and sets the cookie.
2. Send it on every `POST`, `PUT`, `PATCH` and `DELETE`, with the cookie:
   ```js
   fetch("/api/accounts/check_all/", {
     method: "POST",
     credentials: "same-origin",
     headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
     body: JSON.stringify({}),          // or no body at all
   });
   ```
3. On `403 csrf_failed`, fetch a new token (`GET /api/csrf/`) and retry once. On `403 cross_site_request`, don't retry; the request came from the wrong place.

**React during development** (`npm run dev` on `http://localhost:5173`):
- Use Vite's proxy for `/api` and set `CSRF_TRUSTED_ORIGINS=http://localhost:5173`. Production needs none of this: the built app is served by Django from the same origin.

**React Native:**
- Same rules: call `/api/csrf/`, keep the cookie (React Native's `fetch` stores cookies on iOS and Android), and send `X-CSRFToken`. Native apps send no `Origin` or `Sec-Fetch-Site`, so only the token is checked, which is enough because CSRF is a browser attack.
- **A phone can't reach the panel today:** it only listens on 127.0.0.1. Opening it to the network needs a login first; that's future work, not a setting to change.

## 3. Errors

Every error body has a stable `code` (for program logic) and a plain-language `message` (to show). Some errors add more fields:

```json
{"code": "already_watched", "message": "You're already watching @artist.bsky.social.", "fields": {...}}
```

- **`problem`:** `{category, title, explanation, fix}`. `fix` is one of `update_login`, `update_webhook`, `choose_channel`, `check_handle`, `restart_bot`, `wait`, `none`. Categories are listed in ACTIVITY_AND_STATUS.md.
- **`fields`:** errors per field, e.g. `{"handle": ["…"]}`.
- **`detail`:** DRF's own text; kept for compatibility.
- **`retry_after_seconds`,** with a `Retry-After` header.

**Exception:** validation errors on the account and channel forms keep DRF's original shape, `{"field": ["message"]}`, with no `code`. This keeps older clients working.

| HTTP | `code` | When |
|---|---|---|
| 400 | `invalid_json` | The body isn't valid JSON. |
| 400 | `invalid_settings` | Settings form errors (with `fields`); nothing was saved. |
| 400 | `invalid_handle` / `already_watched` | Account form; also field errors under `handle`. |
| 400 | `invalid_filter` | Activity log filters (with `fields`). |
| 400 | `invalid_reassign` | Channel delete with a bad `?reassign_to=`. |
| 400 | `bluesky_not_configured`, `invalid_login_test` | Test login with nothing to test. |
| 400 | (Django) | Unknown `Host` header. Plain text, not JSON: the request is refused before the API runs. |
| 403 | `csrf_failed` | Missing or wrong CSRF token. |
| 403 | `cross_site_request` | The request came from another site. |
| 404 | `not_found` | Unknown address or id. |
| 404 | `check_job_not_found`, `no_check_jobs` | Check jobs (section 4.6). |
| 405 | `method_not_allowed` | Wrong HTTP method. |
| 409 | `already_checking` | `poll_now` while that account is being checked. |
| 409 | `channel_in_use`, `channel_is_default`, `no_default_channel` | Channel delete rules. |
| 415 | `json_required` | A form or text body. |
| 429 | `too_many_tests`, `bluesky_rate_limited` | Login test limits; Bluesky's limits. With `Retry-After`. |
| 500 | `internal` | Unexpected error. Details are only in the server log. |
| 502 / 503 | `bluesky_error`, `bluesky_unavailable`, `settings_not_saved`, … | Bluesky or the database couldn't be used. Safe to retry. |

**Lookups and tests answer negatives with 200.** A missing profile is `200 {result: "not_found"}` and a rejected test login is `200 {result: "rejected"}`. The HTTP status says whether Bluesky could be asked; `result` says what the answer was.

## 4. Endpoints

### 4.1 Status and activity (read-only)

| Request | Response |
|---|---|
| `GET /api/status/` | Overall state (`running`, `problem`, `not_configured`, `unavailable`), the checker's health, setup progress, counts, current problems, recent errors. Full field list in ACTIVITY_AND_STATUS.md. |
| `GET /api/activity/?group=&type=&level=&account=&channel=&since=&before=&page_size=` | Cursor-paged log entries `{id, type, level, message, problem, account, channel, attempt, is_retry, detail, repeat_count, created_at, last_seen_at}`. |
| `GET /api/activity/{id}/` | One entry, plus `technical` (troubleshooting text, secrets removed). |
| `GET /api/csrf/` | `{csrf_token, header_name, cookie_name}`; sets the cookie. |

### 4.2 Settings

| Request | Body | Response |
|---|---|---|
| `GET /api/settings/` | | `{bluesky_handle, has_bluesky_password, default_channel {id, name} \| null, poll_interval_minutes (number), settings_version, updated_at, bluesky_login}` |
| `PATCH /api/settings/` (`POST` also accepted) | Any of `bluesky_handle`, `bluesky_password` (blank = keep), `poll_interval_minutes` (1–60) | `200 {status: "saved", settings: {…as GET}}`, `400 invalid_settings`, `503 settings_not_saved`. Applies at once; no restart. Channels, including the default, are under `/api/webhooks/`. |
| `POST /api/settings/test-login/` | Optional `bluesky_handle`, `bluesky_password` (blank = the saved one) | `{ok, result: ok\|rejected\|app_password_needed\|rate_limited\|unavailable\|error\|invalid\|too_many_tests, code, message, problem, handle, account {did, handle}, matches_saved, saved: false, password_looks_like_app_password, warning, retry_after_seconds}`. Never saves anything. At most 5 tests per 5 minutes; the next one gets `429` with `result` and `code` both `too_many_tests`. A `400` (nothing to test, a bad handle) has `result: invalid`. |

### 4.3 Bluesky profile lookup

`GET /api/bluesky/profile/?handle=…`

- **Answer:** `{query, handle, result: found|not_found|unavailable|invalid|rate_limited|error, exists, profile {did, handle, display_name, avatar_url, description, followers_count, posts_count, watch_as}, already_watched, watched_account_id, code, message, problem, suggestion, retryable, retry_after_seconds, cached}`.
- **HTTP status:** 200, or 400 (invalid), 429 (rate-limited), 502/503 (errors).
- **For a search-as-you-type box:**
  - wait about 300 ms after typing stops;
  - cancel the previous request;
  - ignore answers whose `query` isn't the current text.

### 4.4 Watched accounts

| Request | Body | Response |
|---|---|---|
| `GET /api/accounts/` | | Paged list of accounts (fields below), newest first |
| `POST /api/accounts/` | `handle` (required; `@`, links and DIDs accepted), `display_name`, `channel_id` (null = follow the default), `is_active`, `announce_posts`, `announce_reposts`, `announce_replies` | `201` account; `400 {handle: [...]}` |
| `GET /api/accounts/{id}/` | | The account |
| `PATCH /api/accounts/{id}/` (or `PUT`) | Any of the fields above | The account |
| `DELETE /api/accounts/{id}/` | | `204` |
| `GET /api/accounts/{id}/posts/` | | The posts noticed for this account, newest first, paged (`?page=2`): `{id, post_uri, post_url, text, kind, status, attempts, seen_at, sent_at}`. `status`: `seeded` (noted on the first check, never sent), `pending`, `sending`, `retry`, `sent`, `failed`, `skipped`. |
| `POST /api/accounts/{id}/poll_now/` | | Checks this one account now, inside the request (a few seconds). `{new_posts_announced, was_first_poll, state, state_label, state_message, first_check, problem, check {result, message, new_posts, posts_sent, not_announced}, error?}`; `409 already_checking` |
| `POST /api/accounts/check_all/` | | `202`, a check job (section 4.6) |

**Account fields:**
- `id`, `handle`, `display_name`, `is_active`
- `channel_id`, `channel {id, name, is_default}`, `uses_default_channel`
- `announce_posts`, `announce_reposts`, `announce_replies`
- `first_poll_done`, `created_at`
- Status: `state` (`checking`, `paused`, `error`, `waiting_first_check`, `active`), `state_label`, `state_message`, `checking_now`, `problem`, `first_check {done, at, seeded_posts}`, `last_checked_at`, `last_check_ok`, `last_success_at`, `last_error {at, ongoing, category, title, explanation, fix}`

### 4.5 Discord channels

| Request | Body | Response |
|---|---|---|
| `GET /api/webhooks/` | | Paged list `{id, name, url_masked, is_default, account_count, last_delivery {at, ok, error}, created_at, updated_at}` |
| `POST /api/webhooks/` | `name`, `url` (a Discord webhook URL), `is_default` | `201` channel; `400` field errors (never repeat the URL) |
| `PATCH /api/webhooks/{id}/` | `name`, `url` (leave out, or masked = keep), `is_default` | The channel |
| `DELETE /api/webhooks/{id}/?reassign_to=<id>\|default` | | `204`; `409 channel_in_use` (with `accounts`), `409 channel_is_default`, `409 no_default_channel`, `400 invalid_reassign` |
| `POST /api/webhooks/{id}/make_default/` | | The channel |
| `POST /api/webhooks/{id}/test/` | | `200 {status: "sent"}` or `502 {status: "failed"}` |

### 4.6 Check all (background job)

A Check All runs in the background, so it can take as long as it needs without holding a web request open.

```
POST /api/accounts/check_all/     → 202  {…job…, "created": true}   Location: /api/check-jobs/{id}/
GET  /api/check-jobs/{id}/        → 200  {…job…}                     poll every poll_after_seconds until final
GET  /api/check-jobs/latest/      → 200  {…job…}  or 404 no_check_jobs
GET  /api/check-jobs/?limit=10    → 200  {"results": [{…job…}, …]}  newest first, at most 50
```

**The job:**

```json
{
  "id": "6f1c…",                 "kind": "check_all",     "url": "/api/check-jobs/6f1c…/",
  "status": "running",           "final": false,          "status_label": "Checking",
  "message": "Checking @artist.bsky.social… (1 of 3 done)",
  "result": null,                "problem": null,
  "requested_at": "…", "started_at": "…", "finished_at": null, "updated_at": "…",
  "progress": {"total": 3, "done": 1, "percent": 33, "current": {"id": 2, "handle": "artist.bsky.social"}},
  "counts": {"checked": 1, "failed": 0, "skipped": 1, "already_checking": 0, "waiting": 2, "new_posts": 0, "posts_sent": 0},
  "accounts": [
    {"id": 1, "handle": "a.bsky.social", "result": "checked", "reason": null, "message": "No new posts.",
     "problem": null, "first_check": false, "new_posts": 0, "posts_sent": 0, "not_announced": 0,
     "state": "active", "state_label": "Active"},
    {"id": 2, "handle": "artist.bsky.social", "result": "checking", "message": "Being checked now.", …},
    {"id": 3, "handle": "b.bsky.social", "result": "waiting", "message": "Waiting to be checked.", …},
    {"id": 4, "handle": "paused.bsky.social", "result": "skipped", "reason": "paused", …}
  ],
  "checker_running": null,       "poll_after_seconds": 2
}
```

| `status` | `final` | Meaning |
|---|---|---|
| `queued` | no | Waiting for the checker. `checker_running: false` means the checker is down: the message says so, and the job expires after 2 minutes. |
| `running` | no | `progress` and `accounts` update as each account is done. |
| `finished` | yes | `result`: `ok`, `partial`, `failed`, `nothing_to_check`, `not_configured` or `login_failed`, with `message`, `problem` and the final `counts`. |
| `interrupted` | yes | The checker stopped mid-way (restart, crash). Accounts not reached are `skipped` with reason `stopped`. |
| `expired` | yes | No checker picked it up within 2 minutes. |

**Account rows:**
- `result` is `waiting`, `checking`, `checked`, `failed`, `skipped` (with `reason`: `paused`, `login_failed`, `not_configured`, `time_limit`, `stopped`) or `already_checking` (the scheduled check had it at that moment).
- Rows describe each account as of the check; for live state, read `/api/accounts/`.

**Behavior the panel can rely on:**
- **One at a time:** pressing Check All while one is queued or running returns that job (`created: false`), with the same id.
- **No duplicates:** the job uses the same per-account locks and post claims as the scheduled check, so a post is sent once even when both run at the same time (tested across separate processes).
- **Survives a refresh:** jobs are stored in the database. After a page reload, `GET /api/check-jobs/latest/` shows the same job, still running or finished. The newest 50 finished jobs are kept.
- **One failure stops nothing:** an account that fails is reported and the rest are still checked. Only a login that doesn't work stops the rest (reason `login_failed`).
- **Time limit:** accounts not started within 10 minutes (`CHECK_ALL_TIME_LIMIT_SECONDS`) are left to the next scheduled check (reason `time_limit`).

**Suggested panel flow:**
1. On load, call `latest`. If it isn't `final`, resume polling it.
2. On click, `POST` and poll the `url` every `poll_after_seconds` until `final`.
3. Show `message` and each row's `message`.

**How it runs (no extra services):**
- The web request only writes a `CheckJob` row.
- A thread in the checker process (the scheduler, or the `runserver` process with `start.bat`) looks for queued jobs every 2 seconds. Only the checker holding the scheduler lease runs them, and it claims each with one conditional database update. Code: `bluesky_monitor/jobs.py`.
- **Why not Celery or Redis:** the checker process, the database lease and the per-account locks already provide everything a job queue would. A second service to install and keep running would cut against the "Docker Desktop and one click" goal.

## 5. Addresses outside `/api/` (the control panel)

Django also serves the built control panel (see [FRONTEND.md](FRONTEND.md)):

| Address | What |
|---|---|
| `/`, `/setup`, `/accounts`, `/activity`, `/settings` | The panel's page (`GET` and `HEAD` only). Sets the CSRF cookie. Strict `Content-Security-Policy`, `Cache-Control: no-cache`. |
| `/assets/<file>` | The panel's built scripts, styles and fonts, from `frontend/dist/assets` only. Cached for good (their names change with each build). |
| `/favicon.svg` | The icon. |
| `/third-party-notices.txt` | The license notices of the third-party software the bot ships (`THIRD_PARTY_NOTICES.txt`). |

## 6. Notes for clients

- **The newest Check All job:** `GET /api/check-jobs/?limit=1` answers `{"results": []}` before the first job, instead of a 404.
- **How often the panel polls:**
  - `/api/status/` every 10 s, or 3 s while a check runs;
  - the accounts every 15 s, or 3 s while one is being checked;
  - a running job every `poll_after_seconds`.
- **Removed fields are refused:** sending `discord_webhook_url` to the settings or account API gets `400`, with a message naming the replacement (`channel_id`, or `POST /api/webhooks/{id}/make_default/`). It isn't silently ignored.
- **`poll_interval_minutes`** is a number in `GET /api/settings/`. Requests accept a number or a numeric string.
- **Post history** keeps the newest 1,000 finished posts per account (`SEEN_POSTS_KEPT_PER_ACCOUNT`). Posts still waiting to be sent are never removed.
