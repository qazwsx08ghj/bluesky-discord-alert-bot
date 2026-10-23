# Running the bot with Docker Desktop

The only thing a Windows user installs is **Docker Desktop**. Everything else is double-clicking a `.bat` file.

## 1. The scripts

**Users:** installing, everyday use and troubleshooting are in the [README](../README.md) ([繁體中文](../README.zh-TW.md)). This section describes what the scripts do underneath.

- **`setup.bat`:**
  1. checks Docker Desktop, and starts it if needed;
  2. builds the bot from source, or downloads the image in a release download;
  3. offers to bring over an earlier, non-Docker install in the same folder. It copies `db.sqlite3` (accounts, channels, history) and imports the `.env` settings; the old files are only read. If the old bot is still running, setup stops with a message, because two copies would send every post twice;
  4. starts the bot, waits until the control panel answers, and opens http://127.0.0.1:8000.
- **`start.bat`** starts the bot if it isn't running (running the setup the first time), then opens the panel. **`stop.bat`** stops it; it stays stopped, even after a restart, until `start.bat`. **`update.bat`, `backup.bat` and `restore.bat`:** see section 3.
- **Coming back by itself:** the services restart after a crash, a Docker Desktop restart or a computer restart. The last needs Docker Desktop's "Start Docker Desktop when you sign in".
- **Problems:**
  - every script explains them in plain words and never deletes anything;
  - if the panel doesn't answer in time, a report goes to `logs\problem-report.txt`, with passwords and webhook links hidden.
- **Docker Desktop checks:** installed, running (started and waited for up to 3 minutes), recent enough (Docker Compose 2.23.0 or later, which comes with Docker Desktop 4.25; an older one is explained and nothing is run), and set to Linux containers.
- **When Docker Desktop's engine stops answering** (for example after sleep or an update), the script offers to restart it: Y, or automatically after 60 seconds. It uses `docker desktop restart` where available; on older versions it ends Docker Desktop and all its helper processes and starts it again. It restarts once at most.

## 2. What runs, and where the data is

`docker-compose.yml` runs two services from one image:

| Service | What it runs | Restarts | Health check |
|---|---|---|---|
| `web` | `prepare_data`, then gunicorn with 2 workers (the panel and the API) | `unless-stopped` | `GET /api/health/` (200 when the database is ready and up to date) |
| `checker` | `wait_for_database`, then `run_scheduler` (checks, sending, Check All jobs) | `unless-stopped` | `manage.py checker_health` (heartbeat less than 2 minutes old) |

**All data lives in one named volume, `alertbot_bot-data`, mounted at `/data`:**

| Path | What |
|---|---|
| `/data/db.sqlite3` | Accounts, Bluesky login, channels, settings, activity history, Check All results |
| `/data/backups/` | Automatic backups: before every migration (`db-auto-*`), before every update (`db-before-update-*`) and before every restore (`db-before-restore-*`), plus manual ones (`db-manual-*`); the newest 5 of each kind, plus the newest of each earlier database version (see "Where backups are") |
| `/data/secret_key` | Django's secret key, generated once per install, readable only by the bot |
| `/data/cache/` | Shared cache for the web workers (lookups, login-test limit) |

- **The volume survives** restarts, rebuilds, updates, and even removing the containers. The scripts never run `down`, `volume rm`, `volume prune` or `system prune`, and a test checks that (it also proves the check itself catches those commands). The only clean-up is described in section 3.
- **The volume name is fixed** by `name: alertbot`, so it doesn't depend on the folder the scripts are in.
- **Backups made by `update.bat` and `backup.bat`** are also copied to the `backups` folder next to the scripts, outside Docker. They contain passwords, so keep them private.

**Startup order:**
- `web` runs `prepare_data`: create the secret key once, back up if migrations are waiting, apply them, load the settings. If the backup fails, nothing is migrated and the container stops with the reason in its log.
- `checker` waits until every migration is applied. After a Docker Desktop restart both start at once, and the checker never uses a half-updated database.

**Self-healing:**
- A service that exits is restarted by Docker (`unless-stopped`).
- A checker whose heartbeat stalls for 5 minutes exits on purpose, so Docker restarts it (`SCHEDULER_WATCHDOG_SECONDS`).
- gunicorn replaces stuck web workers after 120 s.

## 3. Updates

`update.bat` does, in order:
1. **Backup:** `backup_db --label before-update`, inside the volume plus a copy in `backups\`. If this fails, nothing else happens.
2. **New version:** a release download pulls the published image (`docker compose pull`); a source folder rebuilds (`docker compose build --pull`). If this fails, the current version keeps running.
3. **Restart:**
   - if the new version differs from the running one, the checker is stopped first (`docker compose stop checker`). It finishes the notification it is sending, so the old version writes nothing while the new one updates the database. If it can't be stopped, the update stops there;
   - `docker compose up -d` recreates the containers with the new image, keeping the same volume. On start, `web` takes the data lock (see "Data safety"), backs up again if the update brings database changes, then migrates.
4. **Check:** waits for `/api/health/`, then opens the panel.
5. **Tidy up:** only after a successful update, removes this bot's old images that nothing uses any more (`docker image prune` limited to images labelled "Bluesky Discord Alert Bot"; never volumes). Without this, every update would leave about 260 MB behind.

If nothing changed since the last update, Docker rebuilds or downloads the same image and leaves the running bot as it is.

### Where backups are
- **Inside Docker,** in the data volume (`/data/backups`): `db-before-update-*` (`update.bat`), `db-manual-*` (`backup.bat`), `db-before-restore-*` (`restore.bat`), and `db-auto-*` (made by the web start whenever migrations are waiting, so an update makes two backups).
- **Which are kept:** the newest 5 of each kind, and, older than those, the newest backup of each earlier database version (up to 5). So the backup taken just before an update that changed the database stays, however often `update.bat` or `backup.bat` runs afterwards: it is the one the older version can go back to.
- **Every backup is checked before it counts:** it is one self-contained file (no journal file beside it), written under a temporary name and renamed only after SQLite's integrity check passed. The copy in `backups\` is compared with it (SHA-256) before it gets its name, so a half-written copy never looks like a backup.
- **In the `backups` folder** next to the script that made it: a copy of each script-made backup. These copies are never removed by the bot.
- `restore.bat` first copies every backup from the volume into its own `backups` folder, so a new download's folder lists the backups made from an older folder too.

### If an update goes wrong
Your data and the backups are untouched; nothing in the update deletes anything, and nothing is undone automatically. What `update.bat` does when a step fails:
- **The backup fails:** it stops; nothing else is done.
- **The new version can't be downloaded:** the current version keeps running.
- **The data is from a newer version than this folder's** (1.0.0-rc.2 and later): it stops before starting anything; see "Older versions and newer data" below.
- **The new version doesn't start, or the panel doesn't answer:** it says so, keeps the backup, and saves `logs\problem-report.txt`.

Then:
1. Run `update.bat` again (a download that broke off is a common cause), or ask for help with `logs\problem-report.txt`.
2. **To go back to the version you had, restore first:** use that version's `restore.bat` and choose the newest `db-before-update-*` backup. It backs up the current data, puts the backup back, and starts that version.
   - A test version's download: the old folder's `restore.bat`.
   - A stable download: set `ALERTBOT_IMAGE` in `docker\compose.env` to the previous version tag (e.g. `…:1.1.0`), then `restore.bat`.
   - Source folder: `git checkout <previous tag>`, then `restore.bat`.

   Just starting the older version doesn't work: it can't use data that the newer version has updated (see below).

### Older versions and newer data
An older version must never run on data a newer version has updated: it can't read it correctly, and must not back it up over, migrate or change it. A newer version leaves migrations in the database that the older one doesn't know; versions from **1.0.0-rc.2** check for them (`botconfig/compat.py`):

| Where | What happens with newer data |
|---|---|
| `start.bat`, `update.bat`, `setup.bat` (from 1.0.0-rc.2) | Before they replace the running bot, they run `manage.py check_compat` with this folder's version. Exit code 3: they stop with a message saying what to do, and the running bot is left alone. `restore.bat` isn't blocked: it is the way back. |
| The web service's start (`prepare_data`) | Stops before the backup and the migrations (exit code 3). `entrypoint.sh` then serves a warning page instead of the panel; `/api/health/` answers `503 newer_data`, which the scripts recognise. Nothing reads or changes the data. |
| The checker | `wait_for_database` never goes on (it checks again every minute, without restarting), and every scheduled check checks again, so nothing is checked, sent or written. |

`update.bat` and `setup.bat` also read which folder the bot was run from before (Docker keeps it on the containers). If it was another folder, they name it at the end and say not to use it any more.

**Limit: 1.0.0-rc.1 has none of this, and nothing in a newer version can change that.** Its scripts and image start it on newer data, and its health check says "ok". Tested on 1.0.0-rc.2 data, with active accounts and a login (and no network for the checker):
- its checker fails on the changed tables and sends nothing;
- its panel can't list, add, change or delete accounts, or delete channels (errors);
- unchanged: accounts, destinations, posts, deliveries and the migration records;
- what does get through: settings saved in its panel, a "Check all" request, a channel added in its panel (saved, although the panel reports an error), the activity log and the checker's status row. The newer version uses these normally afterwards.

The bot stops working until the newer folder's `start.bat` is used again. The user instructions say to delete the old folder after an update, and `update.bat` names it.

### Restoring a backup (`restore.bat`)
1. Copies every backup inside Docker into the `backups` folder (existing files are kept), and lists them all, newest first.
2. You type the number of the backup to put back.
3. **It says what the restore would undo** (`restore_db --report`; nothing is changed yet, and the bot keeps running). A checked copy of the backup is brought up to this version's database layout, then compared with the data now (`botconfig/datastate.py`):
   - things you set up: accounts added, deleted or changed since the backup (by name), Discord channels (by name, never their links), where accounts send, and the bot's settings (never the password);
   - history: posts noticed since the backup (forgotten by a restore, so the next check finds them again and sends them: notifications already sent can appear twice in Discord), and notifications sent, failed or cancelled since;
   - not counted as a loss: bookkeeping the bot redoes by itself (last check times, retries in progress). Activity entries since the backup are mentioned, but don't count on their own.
4. **Confirm:** if nothing would be lost, Y/N as before. If anything would be lost, `RESTORE` must be typed in capital letters; anything else cancels with nothing changed (`ALERTBOT_ASSUME_YES` doesn't count). A backup made by a newer version is refused (this version can't use it), and so is a damaged one.
5. Backs up the current data first (`db-before-restore-*`), so a restore can itself be undone with `restore.bat`.
6. Stops the bot, then checks that **no container uses the data volume any more** (`docker ps --filter volume=...`). One started from another folder, for example, would still write to the database. If one does, nothing is put back, the bot is started again, and the message names that container.
7. Puts the backup back (`manage.py restore_db`): the backup is checked again, then the exclusive data lock must be free (no part of the bot running; see "Data safety"), then the old database's journal files are removed (so they can't be applied to the restored file) and the checked copy replaces the database in one step. No other file is left behind.
8. Starts the bot. If the backup comes from an older version, it is migrated on start (with a backup first, as always).

### Data safety
- **The data lock** (`botconfig/datalock.py`): while the bot runs, the web service (gunicorn's main process, `config/gunicorn_conf.py`) and the checker each hold a shared lock on `/data/.data-access.lock`. Changing the database as a whole needs the exclusive lock:
  - a migration at start (`prepare_data`) waits for it up to `DATA_LOCK_WAIT_SECONDS` (180), e.g. until the old checker has stopped during an update. If the data stays busy, it changes nothing and exits (code 75), and Docker starts it again;
  - a restore refuses at once (after 15 seconds) while any part of the bot runs;
  - a starting checker or web service waits while a migration or restore holds it.

  It is a kernel lock (flock): it ends with its process however that ends (stopped, killed, crashed), so there are no stale locks. Every container on the volume shares Docker's Linux kernel, so the containers see each other's locks. It is one of two safeguards: the scripts also check that no container uses the volume before a restore. That check also covers 1.0.0-rc.2 and older, which don't take the lock.
- **Stopping the checker** (`docker compose stop`, `stop.bat`, updates and restores; `bluesky_monitor/shutdown.py`):
  1. no new check, Discord send or Check All job starts;
  2. the send in progress finishes and its result is saved, so nothing is left in "sending". A running check or Check All stops at the next account and saves what it did (the rest are "not reached", checked next time);
  3. it waits for this at most `CHECKER_STOP_TIMEOUT_SECONDS` (20), below Docker's 30-second limit. Then it exits anyway (code 75) and logs how many notifications were being sent. A second stop signal exits at once.
- **What can't be promised: exactly-once delivery.** A Discord webhook has no way to recognise a repeated message, and the bot can't ask Discord afterwards whether a message arrived. So a notification can appear twice in one channel when:
  - the checker is killed during a send (the time limit above, a crash, power loss), or Discord stored a message but its answer was lost or late. The delivery stays in "sending" and is sent again after `SEND_STALE_SECONDS` (120);
  - a backup is restored (posts noticed after it are found again). `restore.bat` says so before it asks.

  Otherwise each post goes to each channel at most once, and a notification that keeps failing is given up after `SEND_RETRY_WINDOW_HOURS` (24). `config/tests_docker_safety.py` shows both: a clean `docker stop` during a send, and the duplicate after `docker kill`.

## 4. Security inside Docker

- **This computer only:** the panel is published as `127.0.0.1:8000:8000`. gunicorn listens on all addresses *inside* the container, so Docker can forward to it.
- **Host and CSRF checks still work:**
  - The browser's `Host` is `127.0.0.1:8000`, which is in `ALLOWED_HOSTS`.
  - Its `Origin` is `http://127.0.0.1:8000`, which matches.
  - There's no proxy in between, so nothing rewrites either. The Docker tests check Host, Origin, CSRF and JSON-only requests against the real containers.
- **`DEBUG` is off**, set in `docker-compose.yml`.
- **The image holds no secrets:** no `.env`, database or backups (`.dockerignore`, plus a check in the `Dockerfile`). It runs as user `bot`, not root, and the code isn't writable by the bot.
- **The control panel is built inside the image.** A `panel` stage installs the exact Node.js packages from `frontend/package-lock.json` (`npm ci --ignore-scripts`) and builds the React app. Only the built files are copied into the image that runs; it has no Node.js. The page is served with a strict Content Security Policy (see [FRONTEND.md](FRONTEND.md)).
- **Docker uses only `docker\compose.env`**, which holds no secrets. The scripts always pass it with `--env-file`, so Docker never reads the folder's old `.env`. Nothing writes `.env`; setup only reads an old `.env` once, to import it.
- **Container logs** go through the same filter that hides webhook tokens. Docker keeps at most 3 files of 10 MB.

## 5. For developers

- **Build and run from source:** this is the default when `ALERTBOT_IMAGE` in `docker\compose.env` is empty, and the scripts add `docker-compose.build.yml`. After changing code, run `update.bat` (it backs up and rebuilds), or:
  ```
  docker compose --env-file docker\compose.env -f docker-compose.yml -f docker-compose.build.yml up -d --build
  docker compose -p alertbot logs -f
  ```
- **Without Docker:** `dev\setup-without-docker.bat` and `dev\start-without-docker.bat` (the earlier venv scripts). Don't run them together with the Docker version. Without a built panel, `/` shows a page explaining how to build it: run `npm ci` and `npm run build` in `frontend\`.
- **The control panel:** see [FRONTEND.md](FRONTEND.md) for `npm run dev`, the demo server and the frontend tests.
- **Dependencies:** the image installs the exact versions in `requirements-lock.txt` plus gunicorn (`requirements-docker.txt`). Update `requirements-lock.txt` by hand: `pip freeze` would also list the developer tools from `requirements-dev.txt`, which don't belong in the image. Then run the tests, and regenerate the license notices (CONTRIBUTING.md, section 8).
- **Tests:** how to run them, including the opt-in real-container tests and how they're isolated from the real install: [TESTING.md](TESTING.md).

## 6. Tested with

What has and hasn't been tested, including the Docker and release acceptance tests: [TESTING.md](TESTING.md), sections 7 and 9.

- Windows 11 with Docker Desktop 4.25 (Docker Engine 24, Compose v2.23), and later Docker Desktop 4.96.1 (Docker Engine 29.9, Compose v5.6).
- **Docker Compose 2.23.0 is the oldest supported version;** the scripts refuse older ones with a message. The Compose commands the scripts use were checked with Compose 2.23.0, 2.24.3, 2.24.6 and 5.6.0 (each against Docker Engine 29.9). Compose 2.19 failed on one of them.
- Checked by hand: recovery after a full Docker Desktop restart, upgrading an earlier install with `update.bat`, a folder with a Chinese name, and importing a copy of a real first-version install with networking switched off.

## 7. Releases (prebuilt images)

Non-technical users should get the **release download**, so nothing is built on their computer.

1. **Prepare the version:**
   - set it in `frontend/package.json`;
   - in `CHANGELOG.md`, move the `[Unreleased]` notes under `## [1.2.0] - YYYY-MM-DD`;
   - make sure the links at the bottom name the real repository.
2. **Tag it:** `git tag v1.2.0 && git push --tags`.
3. **`.github/workflows/release.yml` runs:**
   1. `docker/release_check.py` checks that the tag, `package.json` and `CHANGELOG.md` agree. A release needs a dated changelog entry and no `OWNER/REPOSITORY` placeholders.
   2. Every CI job runs (`ci.yml`).
   3. The image is built for `linux/amd64` and `linux/arm64`, and pushed as `ghcr.io/<owner>/<repo>:1.2.0` and `:stable`. **ARM (`linux/arm64`) has been tested under emulation only, not on real ARM hardware.**
   4. `BlueskyDiscordAlertBot-stable.zip` is attached to the GitHub release. The release notes are the version's changelog section.
4. **The zip** (`docker/make_release_bundle.py`) contains only:
   - the six scripts and `docker/scripts/common.bat`;
   - `docker-compose.yml`;
   - `docker/compose.env` with `ALERTBOT_IMAGE` set to the published image;
   - `README.txt` and `README.zh-TW.txt`;
   - `LICENSE` and `THIRD_PARTY_NOTICES.txt`.

   Its `setup.bat` and `update.bat` download the image instead of building.
5. **Users update with `update.bat`,** which pulls the newest `:stable`. To stay on one version, set a version tag in `docker\compose.env`.

**Pre-releases for testing:** a tag with a suffix, such as `v1.2.0-rc.1`:
- pushes only `:1.2.0-rc.1`, never `:stable`;
- its zip uses that exact image;
- the GitHub release is marked as a pre-release.

Its changelog entry may still say `Unreleased`. Users on `:stable` never receive a pre-release.

**The GitHub package must be public,** so users can download without signing in to the registry. A new package starts private: change its visibility once, after the first release.
