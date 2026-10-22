# API changes before the first release

> **Historical document.** How the API changed during development steps 7, 9 and 10 (October 2026), before the first public release. No released version had the older behaviour, so clients written for 1.0 or later can ignore this. The current API is in [API.md](../API.md) and [openapi.yaml](../openapi.yaml).

## Step 7: security hardening and background Check All

- **Check All runs in the background.** `POST /api/accounts/check_all/` now answers `202` with a job instead of `200` with the results; the results are on the job. The route was added in step 6 and no client used it yet.
- **New routes:** `GET /api/check-jobs/`, `/api/check-jobs/latest/`, `/api/check-jobs/{id}/` and `/api/csrf/`.
- **Changes need the CSRF token.** The old page (`templates/index.html`) sends it. Clients that didn't send it now get `403 csrf_failed`.
- **Request bodies must be JSON** (`415 json_required` otherwise). Every existing request in the old page already sent JSON.
- **Error bodies:** DRF errors gain `code` and `message` (`detail` is kept). Unknown `/api/` addresses answer with JSON 404s.
- **Removed:** `/admin/`, DRF's browsable HTML pages, and the `createsuperuser` step in `setup.bat`.
- **Defaults:** `DEBUG` is now off unless `.env` says `DEBUG=True`. The default `ALLOWED_HOSTS` also includes `[::1]`.

## Step 9: the React control panel

No API changes. The new panel (`frontend/`, see [FRONTEND.md](../FRONTEND.md)) uses the routes above exactly as documented. Django now also answers these addresses, outside `/api/`:

| Address | What |
|---|---|
| `/`, `/setup`, `/accounts`, `/activity`, `/settings` | The panel's page (`GET` and `HEAD` only). Sets the CSRF cookie. Strict `Content-Security-Policy`, `Cache-Control: no-cache`. |
| `/assets/<file>` | The panel's built scripts, styles and fonts, from `frontend/dist/assets` only. Cached for good (their names change with each build). |
| `/favicon.svg` | The icon. |

Notes for clients:
- The panel reads the newest Check All job with `GET /api/check-jobs/?limit=1`, which answers `{"results": []}` instead of a 404 before the first job.
- Polling: `/api/status/` every 10 s (3 s while a check runs), accounts every 15 s (3 s while one is being checked), a running job every `poll_after_seconds`.

## Step 10: legacy layers removed

The compatibility layers kept for older versions and the old page are gone. Every capability is still there, in the form the panel already used:

| Removed | Use instead |
|---|---|
| `POST /api/settings/test-discord/` | `POST /api/webhooks/{id}/test/` |
| Settings fields `discord_webhook_url`, `has_discord_webhook_url` | `default_channel`; change it with `POST /api/webhooks/{id}/make_default/` |
| Settings field `env_file_in_sync` and the save `warning` | Nothing: settings live only in the database; `.env` is never written |
| Account field `discord_webhook_url` (read and write) | `channel_id` / `channel` |
| Account field `seen_posts` (every post ever noticed, in every list response) | `GET /api/accounts/{id}/posts/`, paged |
| `/classic/` | The panel at `/` |

- Sending a removed field (`discord_webhook_url`) to the settings or account API is refused with `400` and a message naming the replacement, rather than being silently ignored.
- `poll_interval_minutes` in `GET /api/settings/` is now a number (it was a string for older clients). Requests accept either.
- Post history is capped at the newest 1,000 finished posts per account (`SEEN_POSTS_KEPT_PER_ACCOUNT`); posts still waiting to be sent are never removed.

## After 1.0.0-rc.1: several channels per account, and role mentions

- **Accounts:** `channel_id`, `channel` and `uses_default_channel` are replaced by `destinations: [{channel_id, role_id}]` (plus read-only `channel` and `follows_default` per row). Sending `channel_id` is refused with `400` and a pointer to `destinations`.
- **Per-row errors:** `{"destinations": [{}, {"role_id": ["…"]}]}`.
- **Posts:** `attempts` moved to the new `deliveries` list (one per channel); `status` loses `sending` and `retry` (those are delivery states now) and gains `partly_sent`.
- **Activity:** new type `mention_not_sent`, new problem category `mention_refused`.
