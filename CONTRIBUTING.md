# Contributing

**English** | [繁體中文](CONTRIBUTING.zh-TW.md)

This page is for people who change the bot's code. People who only use the bot need just the [README](README.md); they never install Python, Node.js or Git.

The project has two halves that meet at one place, the API:
- **Backend:** Django and Django REST Framework, plus the background checker. See [docs/BACKEND.md](docs/BACKEND.md).
- **Frontend:** the React control panel in `frontend/`. See [docs/FRONTEND.md](docs/FRONTEND.md).
- **The contract between them:** [`docs/openapi.yaml`](docs/openapi.yaml), an OpenAPI 3.1 description of every endpoint, generated from the backend code. The frontend's TypeScript types are generated from it. [docs/API.md](docs/API.md) explains the API in words; for the exact fields and types, the contract is authoritative.

You can work on either half without the other's tools. How the project itself is developed, including its use of AI, is described in [section 10](#10-how-this-project-is-developed).

## 1. Project map

```
activity/            activity log, problems, GET /api/status/
bluesky_monitor/     watched accounts, Bluesky login, the checker (scheduler, poller), Check All jobs
discord_alerts/      Discord channels (webhooks) and sending
botconfig/           settings stored in the database; backup, restore and start-up commands
config/              settings, URLs, security, serving the panel, the API contract (openapi.py)
  testing/           the demo server (fake Bluesky and Discord) and fakes for the script tests
frontend/            the React control panel (built into the Docker image)
docs/                documentation, openapi.yaml (generated), screenshots
docker/              the Docker entry point, the scripts' shared code, the release download builder
dev/                 for developers only: the demo stack in Docker, and running without Docker
*.bat                the Windows scripts users run (setup, start, update, stop, backup, restore)
```

## 2. Frontend only (no Python needed)

You need Node.js 20.19 or newer (22 recommended), and Docker if you want a running demo bot.

```
docker compose -f dev/docker-compose.yml up --build      # the demo bot at http://127.0.0.1:8765/
cd frontend
npm ci
set ALERTBOT_API=http://127.0.0.1:8765                   # PowerShell: $env:ALERTBOT_API="http://127.0.0.1:8765"
npm run dev                                              # the panel at http://localhost:5173, live reload
```

The demo bot is the real backend with made-up accounts, a fake Bluesky and a fake Discord. It is safe by construction:
- it has no access to your `.env`, your database or any folder of yours;
- its container has no internet;
- it can't touch the real bot's containers or data;
- everything is gone after `docker compose -f dev/docker-compose.yml down`.

`DEMO_SCENARIO=empty` shows a first visit; `problem` shows a rejected App Password. Messages "sent" to Discord are listed at http://127.0.0.1:8765/__demo__/discord/.

Without Docker, `npm test` still works: it runs the whole panel against a fake API in a simulated browser.

Types: never write API shapes by hand. `src/api/types.ts` only gives short names to the generated `src/api/schema.gen.ts`. If the panel needs something the API doesn't have, that is a backend change first (section 4).

## 3. Backend only (no Node.js needed)

You need Python 3.11.

```
python -m venv venv
venv\Scripts\activate                      # macOS/Linux: source venv/bin/activate
pip install -r requirements-dev.txt        # the app's exact versions plus the contract tools
python manage.py test                      # every backend test, about 3 minutes
```

`dev\setup-without-docker.bat` does the same and creates a database. `requirements-dev.txt` adds only development tools (drf-spectacular, jsonschema, PyYAML); they are never in the Docker image and never used while the bot runs.

The Django tests check the panel's serving with a temporary build, so they don't need Node.js. To see the panel itself, use the Docker install (`setup.bat`), or build it once with Node.js (`cd frontend && npm ci && npm run build`).

**Never use your real data for development.** Tests use temporary databases and fake credentials. For anything else, point `DATABASE_PATH` at a scratch file, or use the demo server: `python config\testing\demo_server.py`.

## 4. Changing the API

The contract is generated from the code, and the frontend's types from the contract. Both files are committed, so a change to the API shows up in review as a change to `docs/openapi.yaml`.

1. Change the view or serializer, with its tests.
2. If the change adds or changes a field, response or error, describe it in `config/openapi.py`. Request and response shapes are documented there, next to the views; the real views aren't touched.
3. Regenerate and check:
   ```
   python manage.py api_contract --write     # rewrites docs/openapi.yaml
   python manage.py test --tag contract      # every API answer matches the contract
   cd frontend
   npm run api:types                         # rewrites src/api/schema.gen.ts
   npm run typecheck                         # shows every place in the panel affected
   ```
4. Update `docs/API.md` (the words), and the fake API in `frontend/src/test/fakeApi.ts`. Its answers use `satisfies` with the generated types, so they can't drift silently.
5. Commit the code, `docs/openapi.yaml` and `schema.gen.ts` together.

**Rules:**
- **Keep existing behaviour.** Don't change what the API returns just to make the contract simpler: describe what it really does. If the contract and the code disagree, the conformance tests fail; fix the description, unless the code has a bug.
- **Don't break clients.** Adding fields is fine. Removing or renaming fields, or changing a type, breaks clients (the panel and a future mobile app); discuss it first.
- **Don't edit the generated files by hand.** The checks regenerate them and fail on any difference.

`python manage.py api_contract` (without `--write`) and `npm run api:check` only check, and fail if the files are out of date. CI runs both. The output is identical on every computer: sorted, LF line endings, exact tool versions.

## 5. Tests

The full reference is [docs/TESTING.md](docs/TESTING.md): every suite and what it verifies, prerequisites, CI, the release acceptance tests and the known limitations. The most used commands:

| Command | What it checks | Needs |
|---|---|---|
| `python manage.py test` | Every backend test: API, checker, delivery, security, serving the panel, the contract, the Windows scripts (on Windows) | Python |
| `python manage.py test --tag contract` | Every operation's real answers against `docs/openapi.yaml` (closed objects: no undocumented fields), and the file is current | Python |
| `python manage.py api_contract` | `docs/openapi.yaml` matches the code | Python |
| `python docker/third_party_notices.py` | `THIRD_PARTY_NOTICES.txt` matches the exact packages shipped | Python, `npm ci` |
| `npm test` (in `frontend/`) | The panel against a fake API, in a simulated browser | Node.js |
| `npm run typecheck`, `npm run api:check` | TypeScript, and that `schema.gen.ts` matches the contract | Node.js |
| `npm run e2e` | Chromium against the real app with fake Bluesky and Discord | Node.js, Python |
| `RUN_DOCKER_TESTS=1` + `manage.py test config.tests_docker` | The real scripts and containers, on Windows | Docker Desktop |
| `RUN_DOCKER_TESTS=1` + `manage.py test config.tests_dev_stack` | The demo stack: no files, credentials or internet; fake Discord | Docker |

**Every test is isolated:**
- fake credentials;
- temporary databases and folders;
- no real Bluesky or Discord;
- nothing reads or writes your `.env`.

Keep it that way: a test that could reach the real services or real data is a bug.

## 6. Continuous integration

`.github/workflows/ci.yml` runs on every push to `main` and every pull request, in separate jobs:

| Job | Runs on | Checks |
|---|---|---|
| Backend | Linux | `pip-audit`, `manage.py check`, missing migrations, Django tests (except the contract) |
| API contract and notices | Linux | `api_contract`, `test --tag contract`, `npm run api:check`, typecheck, `THIRD_PARTY_NOTICES.txt` up to date |
| Frontend | Linux | `npm audit`, typecheck, `npm test`, build |
| Browser tests | Linux | Playwright against the demo server |
| Windows scripts | Windows | the real `.bat` files under cmd.exe, with a fake Docker |
| Docker | Linux | Compose files, image build (not pushed), image contents, the demo stack's safety |

`release.yml` runs all of them first, and publishes nothing unless every one passes. What each job runs, and the latest verified results: [docs/TESTING.md](docs/TESTING.md), section 6.

## 7. Ground rules

- **Users never need developer tools.** Keep the one-click Windows scripts, the Docker layout, and `127.0.0.1`-only access working.
- **Never delete user data.** The scripts never remove the Docker volume. Every database change is backed up first. Tests check both.
- **Secrets stay secret.**
  - Webhook URLs and passwords never appear in API answers, logs or pages.
  - Only masked forms are shown.
  - Never commit `.env`, databases or backups.
- **Exact versions.** Python versions are in `requirements-lock.txt` (and `requirements-dev.txt`); Node.js versions are in `package-lock.json`. Run the audits after an update.

## 8. License

The project is MIT-licensed ([LICENSE](LICENSE)). By contributing, you agree that your contribution is released under the same license.

When you add a dependency:
- **Check its license first.** MIT, BSD, ISC and Apache-2.0 are fine.
- **Ask before adding anything copyleft** (GPL, AGPL, LGPL) or with unusual terms, if it would ship to users in the Docker image or the built panel. Development-only tools matter less, because they aren't distributed.
- **Fonts, icons and images** need a license that allows redistribution, such as the SIL Open Font License or CC0.
- **Regenerate the notices.** `THIRD_PARTY_NOTICES.txt` holds the license and copyright notices of everything the Docker image and the panel ship, read from the installed packages. After changing `requirements-lock.txt`, `requirements-docker.txt` or the panel's dependencies, install the exact versions and run `python docker/third_party_notices.py --write`. CI, the Django tests and the Docker build fail while the file is out of date.
- **A package that ships no license file** needs its license in `docker/notices/<name>-<version>.txt`, copied from its official source for that version (see `docker/notices/README.md`).

## 9. Pull requests, changelog and translations

- **Pull requests** use the template in `.github/PULL_REQUEST_TEMPLATE.md`. Keep each one to one change, and say how you tested it.
- **Changelog.** Add a line under `[Unreleased]` in [CHANGELOG.md](CHANGELOG.md) for anything a user or API client would notice. Internal clean-ups don't need one.
- **Two languages.** `README.md` and `CONTRIBUTING.md` have Traditional Chinese versions (`*.zh-TW.md`). When you change one, change the other in the same pull request; if you can't write Chinese, say so in the pull request and a maintainer will update it. The tests check that both versions have the same sections, links and commands. Terms are listed in [docs/GLOSSARY.zh-TW.md](docs/GLOSSARY.zh-TW.md). The other documents are in English only.
- **Security problems** are never reported in public issues: see [SECURITY.md](SECURITY.md).

## 10. How this project is developed

This project uses **human-directed, AI-assisted agentic development**.

**Claude Code (an AI coding agent) does most of the hands-on work:**
- writing and changing the code;
- writing and running the automated tests;
- debugging;
- writing the documentation.

**The human maintainer directs and decides:**
- defines what the bot should do and the constraints the work must respect (for example: never touch real user data, keep the one-click Windows install);
- reviews the proposed approach before larger changes;
- evaluates the test results;
- explicitly approves important operations, such as merges, releases, repository settings and migrating real data.

**Not every line of AI-written code has been read by a human.** Confidence in the code comes mainly from:
- the automated tests: the backend, the API contract, the control panel, browser tests, the Windows scripts and real Docker containers;
- CI, which runs them on every change;
- the safety rules in section 7;
- the maintainer's review of approaches and results.

**How a change gets from idea to `main`:**
1. **Requirements:** the maintainer states what's wanted and the constraints. For example: don't modify real data, don't push or publish without approval.
2. **Plan:** for anything larger, Claude Code first presents a plan: the files to change, how it will be tested, and the risks. The maintainer reviews it before work starts.
3. **Implementation:** the work is done on a separate local branch, with tests written or updated alongside the code.
4. **Local checks:**
   - the affected suites run, then the full regression set from [docs/TESTING.md](docs/TESTING.md);
   - the opt-in real-Docker tests run when deployment is affected;
   - every commit is scanned beforehand for real credentials and personal data.
5. **Report:** Claude Code reports what changed, the test results, failures and their causes, and anything not tested. A test that wasn't run is reported as not run, never as passed.
6. **Approval:** the maintainer reviews the report and decides. Merging, pushing, repository settings, releases and anything touching real data happen only with the maintainer's explicit approval.
7. **CI:** after a push, all CI jobs must pass. A failure is investigated, and its cause explained, before more changes are made ([docs/TESTING.md](docs/TESTING.md), section 8).

If you find a problem, please report it. For security problems, follow [SECURITY.md](SECURITY.md).

**AI help in your contributions is welcome.**
- Describe any substantial AI assistance in your pull request (the template asks); a short note is enough.
- This is not a reason to reject a contribution. Every pull request is reviewed the same way: correctness, security, maintainability, and appropriate tests.
- You're responsible for what you submit: understand it, test it, and make sure you have the right to contribute it under the MIT License.
- Never paste secrets or real user data (App Passwords, webhook URLs, `.env` files, databases, problem reports) into an AI tool.
