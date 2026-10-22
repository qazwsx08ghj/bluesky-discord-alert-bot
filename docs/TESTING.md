# Testing and verification

How this project is tested: the tools, what each suite checks, how to run everything yourself, what CI runs, which acceptance tests are manual, and what hasn't been tested yet. For setup and the contribution rules, see [CONTRIBUTING.md](../CONTRIBUTING.md).

Counts and results below are as of **2026-10-22** (updated with the terminology change); the commands always give the current numbers.

## 1. Overview

| Layer | Tool | Tests | Runs locally | Runs in GitHub Actions |
|---|---|---|---|---|
| Backend: API, checker, delivery, security, settings, serving the panel | Django's test runner (`unittest`) | 500 in total, including the rows below | Yes | Yes (Linux), except the Windows-only and opt-in tests |
| API contract conformance | Django tests (tag `contract`), `jsonschema` | 19 | Yes | Yes |
| Third-party notices | `docker/third_party_notices.py` + Django tests | 14 | Yes | Yes |
| Documentation, templates, release check | Django tests (`config/tests_docs.py`) | 21 | Yes | Yes |
| Wording ("notification", no development history in code or the API contract) | Django tests (`config/tests_wording.py`) | 4 | Yes | Yes |
| Windows `.bat` scripts (with a fake Docker) | Django tests run under `cmd.exe` | part of `config/tests_deploy.py` (45) | Windows only | Yes (`windows-latest`) |
| Real Docker install via the Windows scripts | Django tests, opt-in | 11 (`config/tests_docker.py`) | Windows + Docker Desktop only | **No** |
| Development stack safety | Django tests, opt-in | 6 (`config/tests_dev_stack.py`) | With Docker | Yes (Linux) |
| Control panel against a fake API | Vitest, Testing Library, MSW, jsdom | 51 | Yes | Yes |
| Control panel in a real browser against the real app | Playwright (Chromium) | 15 | Yes | Yes |
| Types and generated files | `tsc`, `openapi-typescript --check`, `manage.py api_contract` | | Yes | Yes |
| Known vulnerabilities | `pip-audit`, `npm audit` | | Yes | Yes |
| Release acceptance | mostly manual (section 7) | | Partly | Partly |

## 2. Prerequisites

- **Python 3.11** with `pip install -r requirements-dev.txt`:
  - the image's exact packages;
  - the contract tools (drf-spectacular, jsonschema, PyYAML);
  - gunicorn, which is installed so the license check can read it, but is never run on Windows.
- **Node.js 20.19 or newer** (CI uses 22) and `npm ci` in `frontend/`.
- **For browser tests:** `npx playwright install chromium` once.
- **For the opt-in Docker tests:** Docker (Docker Desktop on Windows). `config/tests_docker.py` also needs Windows, because it drives the real `.bat` scripts.
- **`pip-audit`** isn't in the requirements files. CI installs the latest version each run (`pip install pip-audit`).

No credentials are needed for anything in this document. Section 5 explains why no test can use yours.

## 3. Running the tests

From the project folder:

```
python manage.py test                              # every backend test (Windows also runs the .bat tests)
python manage.py test --tag contract               # only the API contract conformance tests
python manage.py test --exclude-tag contract       # everything else (what CI's backend job runs)
python manage.py test config.tests_deploy          # Docker files and the Windows scripts
python manage.py check
python manage.py makemigrations --check --dry-run  # no model change without a migration
python manage.py api_contract                      # docs/openapi.yaml matches the code
python docker/third_party_notices.py               # THIRD_PARTY_NOTICES.txt matches the installed packages
python docker/release_check.py v1.0.0-rc.1         # the tag, package.json and CHANGELOG.md agree
pip-audit -r requirements-docker.txt
pip-audit -r requirements-dev.txt
```

The control panel, in `frontend/`:

```
npm ci
npm run typecheck                                  # TypeScript
npm run api:check                                  # src/api/schema.gen.ts matches docs/openapi.yaml
npm test                                           # Vitest: the panel against a fake API
npm audit --audit-level=moderate
npm run build
npx playwright install chromium
npm run e2e                                        # builds, starts the demo server, runs Chromium
```

The opt-in real-Docker tests build images and start containers (about 5 minutes). In `cmd.exe`:

```
set RUN_DOCKER_TESTS=1
python manage.py test config.tests_docker config.tests_dev_stack
```

Docker files only:

```
docker compose --env-file docker/compose.env -f docker-compose.yml -f docker-compose.build.yml config --quiet
docker compose -f dev/docker-compose.yml config --quiet
docker build -t bluesky-alert-bot:local .          # also checks the license notices inside the image
```

## 4. What each suite verifies

**Backend (Django):**

| Module | Tests | Verifies |
|---|---|---|
| `activity/tests.py` | 68 | Activity log, problems and their fixes, `GET /api/status/` |
| `bluesky_monitor/tests.py` | 19 | Watched accounts and the poller |
| `bluesky_monitor/tests_delivery.py` | 19 | Claim-before-send, retries, interrupted sends, reading feeds |
| `bluesky_monitor/tests_jobs.py` | 21 | Check All as a background job |
| `bluesky_monitor/tests_processes.py` | 8 | Real separate Python processes sharing one SQLite file: locks, no duplicate notifications |
| `bluesky_monitor/tests_scheduler.py` | 14 | Scheduler lease, restarts, standby takeover |
| `bluesky_monitor/tests_session.py` | 15 | The shared Bluesky session: reuse, refresh, expiry, wrong password, network trouble |
| `bluesky_monitor/tests_step6.py` | 50 | Profile lookup, login test, Check All, per-account status, post/repost/reply options |
| `botconfig/tests.py`, `tests_step8.py` | 27, 17 | Settings in the database, the one-time import, backup and restore, Docker start-up commands |
| `discord_alerts/tests.py`, `tests_channels.py` | 9, 46 | Webhooks and sending (Discord is always mocked), named channels |
| `config/tests_security.py` | 32 | CSRF, Origin and host checks, JSON-only bodies, headers, no admin |
| `config/tests_frontend.py` | 16 | Serving the built panel: headers, CSP, file rules, the notices page |
| `config/tests_openapi.py` | 19 | Every API operation's real answers against `docs/openapi.yaml` (tag `contract`) |
| `config/tests_notices.py` | 14 | `THIRD_PARTY_NOTICES.txt` is complete, current and shipped everywhere |
| `config/tests_docs.py` | 21 | Links, the English and Chinese documents, the archive, templates, the release check |
| `config/tests_wording.py` | 4 | The panel and backend messages say "notification"; no development-step labels in code or the API contract |
| `config/tests_deploy.py` | 45 | Docker files, the dev stack's settings, and the real `.bat` scripts under `cmd.exe` with a fake Docker (Windows only) |
| `config/tests.py`, `tests_step10.py` | 9, 10 | Settings API, removed routes, migrations of legacy data |
| `config/tests_docker.py` | 11 | Opt-in, with **real containers** driven by the real scripts. It covers: `setup.bat` importing an earlier install, `start.bat`, `update.bat` backing up first, `stop.bat` and `restore.bat`; local-only access and CSRF in Docker; crash recovery; data kept across restarts, rebuilds and recreation; and the `@docker` browser checks. (`backup.bat` is covered by the fake-Docker script tests only.) |
| `config/tests_dev_stack.py` | 6 | Opt-in: the demo stack has no mounts, credentials or internet, and Discord is fake |

**Control panel:**
- **Vitest** (`frontend/src/test/`, 51 tests) runs the whole panel in jsdom against a fake API (MSW). The fake API answers are type-checked against the generated contract types, and any request it doesn't know fails the test. The tests cover:
  - setup, accounts, Check All, settings, channels and activity;
  - CSRF retry, offline, secrets and keyboard use.
- **Playwright** (`frontend/e2e/`, 15 tests) runs Chromium against the real Django app and checker started by `config/testing/demo_server.py`. It covers:
  - the setup-to-first-account flow and Check now sending a post;
  - Check All surviving a reload;
  - problems and their fixes;
  - keyboard-only use, tablet sizes and the CSP;
  - no secrets in pages;
  - the license notices page.

  The three `@docker` tests also run against real containers from `config/tests_docker.py`.

**API contract and license notices:**
- `manage.py api_contract` regenerates `docs/openapi.yaml` in memory and fails on any difference; `npm run api:check` does the same for the TypeScript types.
- `docker/third_party_notices.py` rebuilds the notices from the installed packages and fails on any difference. The Docker build runs it inside the image (`--check --python-only`), so an image can't be built with outdated notices.

## 5. Test isolation

Tests never touch real data or real services:
- **Databases:** Django uses a separate test database. Tests that need files or several processes create temporary folders.
- **Fakes:** Bluesky and Discord are mocked or faked everywhere.
- **The demo server** (`config/testing/demo_server.py`):
  - blanks every credential before Django starts, and refuses to start otherwise;
  - uses a temporary database;
  - refuses connections that leave the computer.
- **Tests that start other processes** replace the credential settings with blank or fake values, so a real `.env` is never used.
- **The opt-in Docker tests:**
  - they use their own Docker project names (`alertbot-test-…`), volumes and ports, and remove everything at the end;
  - `config/tests_docker.py` works in a temporary copy of the project that has no `.env`;
  - `config/tests_dev_stack.py` mounts nothing into its containers, and the demo container has no internet at the Docker level.

## 6. Continuous integration

`.github/workflows/ci.yml` runs on every push to `main`, on pull requests, and when `release.yml` calls it:

| Job | Runner | Runs |
|---|---|---|
| Backend | Linux | `pip-audit` (both requirement files), `manage.py check`, missing-migration check, `test --exclude-tag contract` |
| API contract and license notices | Linux | `api_contract`, `test --tag contract`, `npm run api:check`, typecheck, `third_party_notices.py --check` |
| Frontend | Linux | `npm audit --audit-level=moderate`, typecheck, `npm test`, build |
| Browser tests | Linux | Playwright against the demo server; traces are uploaded when a test fails |
| Windows scripts | Windows | `test config.tests_deploy` (the real `.bat` files under `cmd.exe`, with a fake Docker) |
| Docker | Linux | Compose files valid, image build (not pushed), image contents (no Node.js, no developer tools, no private files), `config.tests_dev_stack` |

`release.yml` runs `docker/release_check.py` and all of the jobs above before it builds or publishes anything. It hasn't run yet: no release tag has been pushed.

**Last verified on GitHub:** CI run 39726933241 on commit `39685fa`, with all six jobs passing. The run before it (39724879529) failed two jobs; section 8 explains why.

## 7. Release acceptance tests

| Test | Kind | Status (2026-10-22) |
|---|---|---|
| Release check (`docker/release_check.py`): pre-release accepted; final release refused without a date | Automated (`config/tests_docs.py`) | Passing locally and in CI |
| Pre-release never moves `:stable` (workflow logic) | Automated (static check of `release.yml`) | Passing |
| Release rehearsal for `v1.0.0-rc.1`, using a private registry on 127.0.0.1 instead of ghcr.io | Manual, run locally with a script that isn't in this repository | Passed 20/20 checks. They covered the zip's contents, both READMEs, LICENSE and notices; a fresh install in a folder with spaces and Chinese characters; the correct version; the notices page; and update, stop and start. |
| ARM image (`linux/arm64`) | Manual | Built and started **under emulation only**: health OK, `aarch64`, panel and notices served. **Not tested on real ARM hardware.** |
| Downloaded-file warning (Mark-of-the-Web) | Manual | Verified: Windows' zip extraction marks the `.bat` files, and **Unblock** on the zip prevents that. The SmartScreen dialog itself needs a person to click, and **hasn't been checked**. |
| Live connectivity with real Bluesky and Discord | Manual, only with the maintainer's explicit approval | Done once, in an isolated test install with its own temporary database: login test, one labelled test message, and a 35-minute watch with no duplicates. Contributors can't repeat this; it needs the maintainer's credentials. |
| Real `release.yml` run with a pre-release tag | Needs GitHub | **Not yet run** |
| Install from a published download on a clean machine | Needs a published release and a second PC or VM | **Not yet run** |
| Update from one published version to the next | Needs two published versions | **Not yet run** (rehearsed locally only) |
| **Windows reboot recovery** | Manual | **Deferred.** It needs a reboot of the maintainer's PC, which hasn't been approved yet. |

## 8. When a test fails

1. **Reproduce it.** Run the failing test locally with the command from section 3. For CI, read the failed job's log on GitHub, or run `gh run view <run-id> --log-failed`; the browser job uploads Playwright traces.
2. **Find the cause, not just a way to make it pass.** Don't loosen an assertion, add a skip, or raise a timeout without knowing why it failed.
3. **Add a regression test that fails before the fix and passes after it.** Check that it really fails on the old code.
4. **Run the affected suites again,** then the whole set from section 3.

**A real example: the first CI run** (39724879529) failed two jobs.
- **Browser test:** GitHub's slower machines exposed a real bug. The Setup page rebuilt its login form when the saved settings arrived late, wiping what had been typed.
  - **Fix:** wait for the settings before showing the form.
  - **Regression test:** `setup.test.tsx`, "never loses what was typed when the saved settings arrive late". It delays the settings response, and was checked to fail on the old page.
- **Frontend audit:** a new advisory against Vitest, a development-only test tool, published after the last local audit.
  - **Fix:** upgrade Vitest to 4.1.12.

## 9. Known limitations

- **Windows reboot recovery isn't tested** (deferred, see section 7).
- **ARM is tested under emulation only,** never on real ARM hardware.
- **`config/tests_docker.py` runs only locally on Windows,** not in GitHub Actions. GitHub's Windows runners don't run Docker Desktop with Linux containers.
- **Manual-only checks:**
  - the SmartScreen dialog;
  - a clean-machine install;
  - an update between published versions.
- **The release rehearsal and ARM smoke test** were run with local scripts that aren't in this repository, so they aren't repeatable from the repository yet.
- **Real Bluesky and Discord** are only exercised in rare, approved live tests. Everything else uses fakes, so changes in those services' behaviour would show up in production first.
- **Code review:** not every line of AI-written code has been read by a human (CONTRIBUTING.md, section 10). These tests, CI and the maintainer's review of approaches and results are the main safeguards.
- **No automated secret scanning:** CI doesn't scan for secrets; it's a manual step (section 10). `actionlint` (workflow lint) was also only run by hand during release preparation.

## 10. Secret checks (manual)

CI doesn't scan for secrets, so check your changes yourself before committing. What protects you already:
- `.gitignore` excludes `.env`, databases, `backups/` and `logs/` (problem reports);
- the tests check that the Docker image, the release download and the files the panel serves contain none of them.

**Without extra tools:** read what you're about to commit, and make sure it holds no App Passwords, webhook URLs or real data:

```
git status
git diff --cached
```

**With Docker:** [gitleaks](https://github.com/gitleaks/gitleaks), a secret scanner, runs in a throwaway container; nothing is installed. From the project folder in `cmd.exe` (in PowerShell, write `${PWD}` instead of `%cd%`):

```
docker run --rm -v "%cd%:/repo:ro" zricethezav/gitleaks:v8.21.2 git /repo --pre-commit --staged --redact
docker run --rm -v "%cd%:/repo:ro" zricethezav/gitleaks:v8.21.2 git /repo --redact
```

The first command scans only your staged changes; the second scans the whole history. Both should end with `no leaks found`. `--redact` keeps any finding out of the output.

`.gitleaksignore` lists the two known false positives: fake login tokens built in the tests from made-up data. Add to it only after checking that a finding really is fake, with a comment saying why.

**Verified on 2026-10-22, locally:**
- the full-history scan reports `no leaks found`;
- the staged scan caught a fake access token planted in a staged file;
- the maintainer's own pre-commit check found none of the real credential values in the history.

If a real secret was ever committed or pushed, removing it from the code isn't enough. Cancel it at its source: delete the App Password in Bluesky, or the webhook in Discord, and create a new one.

## 11. Code scanning (CodeQL)

GitHub's CodeQL "default setup" analyses Python, JavaScript/TypeScript and the workflows on every push, on pull requests and weekly. It needs no workflow file. Results are on the repository's **Security → Code scanning** page. It isn't a required check.

The first analysis (2026-10-22, commit `993ba76`) raised 9 alerts:
- **Fixed:** the two real ones.
- **Dismissed:** the other seven, each with the reason below, after checking the code and the tests. CodeQL rules aren't disabled, and nothing is suppressed globally; a new instance of any of these patterns is reported again.

| Alert | Rule | Where | Disposition |
|---|---|---|---|
| #1–3 | Path injection | `config/frontend.py` (serving panel files) | **False positive.** The file name must match `^[A-Za-z0-9][A-Za-z0-9_.-]{0,150}$` (no slashes), `..` is refused, and the resolved path must be directly inside `frontend/dist/assets`. Only listed file types are served. `config/tests_frontend.py` tries traversal (`..`, encoded slashes, `.env`, database names). |
| #4 | Exception information exposed | `config/frontend_views.py` (saving settings) | **False positive.** The message returned is from `SettingsSaveError`, which only ever carries two fixed sentences written for users. Database errors are logged by type only, never returned. |
| #5 | CSRF protection disabled | `config/settings.py` | **False positive.** Django's `CsrfViewMiddleware` is replaced by `config.security.RequestGuardMiddleware`, which runs Django's own CSRF check and adds Origin, `Sec-Fetch-Site` and JSON-only checks for the API too (`config/tests_security.py`). |
| #6 | Clear-text storage of sensitive data | `config/tests_docker.py` | **Used in tests.** The test writes a fake earlier install's `.env`, with fake credentials, into a temporary folder. |
| #7 | Weak hashing of sensitive data | `bluesky_monitor/bluesky.py` (`_fingerprint`) | **Won't fix (accepted).** SHA-256 of the handle and App Password is only a fingerprint, compared to notice when the saved login has changed. It doesn't protect or verify a password. The App Password itself must be stored readable in the same local database, to sign in to Bluesky, so the fingerprint exposes nothing more. |
| #8 | DOM text read as HTML | `docs/history/prototype/control-panel.html` | **Fixed.** The archived prototype inserted avatar initials from typed text without escaping (two places); they're now escaped like the names. The file isn't shipped or served. |
| #9 | Useless regex escape | `frontend/e2e/docker.spec.ts` | **Fixed.** `\.` in a template string lost its backslash, so the test matched any character instead of a literal dot. `config/tests_frontend.py` now checks every `new RegExp(` template string for this. |

Before dismissing a future alert, record its reason here, and use the matching GitHub dismissal reason: false positive, used in tests, or won't fix.
