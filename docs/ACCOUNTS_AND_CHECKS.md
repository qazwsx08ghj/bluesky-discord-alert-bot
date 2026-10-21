# Profile lookup, login test, Check all, and account status (step 6)

Code: `bluesky_monitor/profiles.py`, `check_all.py`, `account_status.py`, `poller.py` (`check_accounts`), `bluesky.py` (`try_login`), `config/frontend_views.py` (`test_login`). Tests: `bluesky_monitor/tests_step6.py`, `tests_processes.py` (`CheckAllAcrossProcessesTests`), `tests_rollback.py`.

One rule for all three new endpoints: **the HTTP status says whether Bluesky could be asked, and `result` says what the answer was.** A negative answer (not found, wrong password) is a `200`, so a search-as-you-type box doesn't log an error for every keystroke. Every body carries `code` and a plain-language `message`, plus `problem` (`{category, title, explanation, fix}`) when there is something to fix.

## `GET /api/bluesky/profile/?handle=…`

Used by the "Add account" search.
- **No login needed:** it reads Bluesky's public API (`BLUESKY_PUBLIC_API_URL`), so it works before the bot's login is set up. It never uses the bot's credentials or session, and it doesn't use up the bot's Bluesky limits.
- **Accepted input:** `name.bsky.social`, `@Name.bsky.social`, a `bsky.app/profile/…` link, or a DID. Uppercase letters and invisible characters are cleaned up.

| HTTP | `result` | `exists` | Meaning |
|---|---|---|---|
| 200 | `found` | true | `profile: {did, handle, display_name, avatar_url, description, followers_count, posts_count, watch_as}` |
| 200 | `not_found` | false | No account with that handle (`code: handle_not_found`) |
| 200 | `unavailable` | true | Deactivated, suspended, or blocks the bot (`account_unavailable`). It can still be added. |
| 400 | `invalid` | null | Not a handle (`invalid_handle`). `suggestion` offers e.g. `alice.bsky.social` for `alice`. |
| 429 | `rate_limited` | null | `retry_after_seconds` plus a `Retry-After` header. Bluesky isn't asked again until then. |
| 503 / 502 | `error` | null | Bluesky couldn't be reached, or sent something unexpected. `retryable: true`. |

Every answer also has:
- `already_watched` and `watched_account_id`
- `query`: the text exactly as sent

**For a debounced search in the React app:**
- Wait about 300 ms after typing stops before sending.
- Cancel the previous request with an `AbortController`.
- Ignore any answer whose `query` isn't the current input text.
- On an invalid input, the 400 shows the message and the suggestion without any network call.

Found, not-found and unavailable answers are cached for 60 s (`PROFILE_LOOKUP_CACHE_SECONDS`), so retyping the same handle doesn't ask Bluesky again (`cached: true`). Errors are not cached.

`watch_as` is the value to send as `handle` when adding the account. Normally it is the handle; for an account whose handle no longer checks out, it is the account's DID.

## `POST /api/settings/test-login/`

Body, both optional: `bluesky_handle`, `bluesky_password`. A blank field means "use the saved one", so the saved login can be tested without retyping the password.

**Separate from Save:**
- The test signs in once with a throwaway client.
- The saved handle, the password, the saved session and its back-off are never changed by a failed test.
- The response always has `saved: false`, and `matches_saved` says whether the tested login is the saved one. The panel shows "It isn't saved yet: press Save" when it's a new one.

**One deliberate exception:** if the *saved* login is tested and works while the bot is waiting after earlier failures, that wait ends. The next check then signs in instead of waiting up to an hour, and a `login_recovered` entry is logged.

| HTTP | `result` | `code` |
|---|---|---|
| 200 | `ok` | `login_ok`. `account: {did, handle}` |
| 200 | `rejected` | `bluesky_login_rejected` (or `account_unavailable` for a suspended account) |
| 200 | `app_password_needed` | `bluesky_app_password_needed`: the account needs an email code, so it must use an App Password |
| 429 | `rate_limited` | `bluesky_rate_limited`, with `Retry-After` |
| 429 | (error) | `too_many_tests`: more than 5 tests in 5 minutes (`LOGIN_TEST_LIMIT`, `LOGIN_TEST_WINDOW_SECONDS`). This protects the sign-ins the bot itself needs. |
| 503 / 502 | `unavailable` / `error` | Bluesky couldn't be reached, or answered unexpectedly |
| 400 | `invalid` | `bluesky_not_configured` (nothing to test) or `invalid_login_test` |

**Also in the response:**
- `password_looks_like_app_password`, plus a `warning` when it doesn't look like one. The password itself is never returned.
- If Bluesky is already limiting the bot's sign-ins, the test answers `rate_limited` without asking Bluesky.

**Secrets:** the password and session tokens never appear in responses, logs or activity entries. A test checks this for success, rejection, network errors and unexpected errors.

**Login state in settings:** `GET /api/settings/` now includes `bluesky_login`. It is the same as in `/api/status/`: `ok`, `failed`, `rate_limited`, `not_configured`, or `unknown` (not tried yet).

## `POST /api/accounts/check_all/`

Checks every active account and gives a result for each.

**Since step 7 this runs in the background.** The request answers `202` with a job, and the results below appear on the job at `GET /api/check-jobs/{id}/` as each account is done. See `docs/API.md` section 4.6 for the job format, statuses and polling. The rules below are unchanged.

**No duplicates:**
- It runs `poller.check_accounts()`, the same code as the scheduled check, so the same per-account lock, claim-before-send and retry rules apply.
- An account that the scheduled check (or a second click) is already checking comes back as `already_checking`; it isn't checked twice.
- A cross-process test runs a scheduled check and three "Check all" requests at the same moment and confirms that each post is sent once.

**One failure never stops the others:**
- A failed feed read, or even an unexpected error such as a bug or a database hiccup, is recorded on that account only.
- This now also applies to scheduled checks. Before, an unexpected error stopped the whole check.
- Only a login that doesn't work stops the rest, because then nothing can be read.

**The finished job:**
- `result`: `ok`, `partial`, `failed`, `not_configured`, `login_failed` or `nothing_to_check`
- `message` and `problem`
- `started_at` and `finished_at`
- `counts`: `{checked, failed, skipped, already_checking, waiting, new_posts, posts_sent}`
- `accounts`: one row per account, paused ones included, in the order they were added

Each row has:
- `result`: `checked`, `failed`, `skipped` or `already_checking` (and, while the job runs, `waiting` or `checking`)
- `reason`, for skipped rows: `paused`, `login_failed`, `not_configured`, `time_limit` or `stopped`
- `message` (plain language) and `problem`
- `first_check`, `new_posts`, `posts_sent` and `not_announced`
- the account's `state` after the check

**Time limit:** new accounts stop being started after 10 minutes (`CHECK_ALL_TIME_LIMIT_SECONDS`; it was 50 s while the check ran inside the web request). Accounts not reached are skipped with reason `time_limit`, and the next scheduled check includes them.

**`poll_now`** keeps its fields (`new_posts_announced`, `was_first_poll`, `error`, and `409 already_checking`) and adds `state`, `state_label`, `state_message`, `first_check`, `problem` and `check`.

## Account status (in every account response)

| `state` | When (first match wins) |
|---|---|
| `checking` | A check of this account is running now (its lock is held) |
| `paused` | Switched off by the user |
| `error` | The latest check failed. `problem.severity` is `warning` for problems that usually clear by themselves. |
| `waiting_first_check` | Added, but not checked yet |
| `active` | Checked normally |

Each account response also has:
- `state_label` and `state_message`
- `checking_now`
- `first_check: {done, at, seeded_posts}`
- `last_checked_at` and `last_check_ok`
- `last_success_at`: kept through failures
- `last_error: {at, ongoing, category, title, explanation, fix}`: kept after recovery, with `ongoing: false`

Times are timestamps, never "3 minutes ago" text, so they don't go stale between refreshes. Everything comes from the account row, so listing accounts needs no extra queries.

## What gets announced (decision 1)

- **Settings per account:** `announce_posts` (default on), `announce_reposts` (on) and `announce_replies` (off). They can be set when adding or editing an account.
- **Kinds:** a feed item is a repost when it carries a repost reason, and a reply when `record.reply` is set. A pinned post counts as a post.
- **Switched-off kinds:** items of a switched-off kind are recorded as `skipped`. Switching a kind on later never sends old items.
- **Discord wording:** reposts say "X reposted" and name the original author; replies say "New reply from X". Plain posts look exactly as before.
- **Threads:** a thread's later posts are replies to the first one, so with replies off only the first post of a thread is announced.

## Handles when adding or editing accounts

- `handle` is cleaned the same way as in the lookup and stored that way, e.g. `@Artist.bsky.social` becomes `artist.bsky.social`.
- A handle that's already watched, in any spelling, is refused with "You're already watching @…".
- A bare name gets the `.bsky.social` suggestion.
- Accounts are not looked up on Bluesky when they're added, so adding works even while Bluesky is unreachable. The panel uses the lookup first.

## Database changes (reversible)

- **`bluesky_monitor` 0005, new nullable columns:**
  - `MonitoredAccount`: `announce_posts`, `announce_reposts`, `announce_replies`, `first_checked_at`, `first_check_posts`, `last_success_at`, `last_error_at`, `last_error_category`
  - `SeenPost`: `kind`
- **Rollback safety:** all columns are nullable, so step 5 code can still add accounts and posts. Missing announce values mean the defaults, and posts without a kind are sent as plain posts.
- **Backfill:** existing accounts get the announce defaults. A working step 5 check becomes `last_success_at`, and a failing one becomes `last_error_*`.
- **Rollback to step 5:**
  ```
  python manage.py migrate bluesky_monitor 0004
  ```
  Then check out the step 5 commit. Without the schema rollback, step 5 code also runs unchanged on the step 6 database; `Step5RollbackTests` runs the real step 5 code to check this.
- The backup taken before this step is in `backups/2026-10-21_before-step-6/`.
