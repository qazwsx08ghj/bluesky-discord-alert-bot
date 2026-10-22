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
- **Docker Desktop checks:** installed, running (started and waited for up to 3 minutes), recent enough, and set to Linux containers.
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
| `/data/backups/` | Automatic backups: before every migration (`db-auto-*`), before every update (`db-before-update-*`) and before every restore (`db-before-restore-*`), plus manual ones (`db-manual-*`); the newest 5 of each kind |
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
3. **Restart:** `docker compose up -d` recreates the containers with the new image, keeping the same volume. On start, `web` backs up again if the update brings database changes, then migrates.
4. **Check:** waits for `/api/health/`, then opens the panel.
5. **Tidy up:** only after a successful update, removes this bot's old images that nothing uses any more (`docker image prune` limited to images labelled "Bluesky Discord Alert Bot"; never volumes). Without this, every update would leave about 260 MB behind.

If nothing changed since the last update, Docker rebuilds or downloads the same image and leaves the running bot as it is.

### If an update goes wrong
Your data and the backups are untouched; nothing in the update deletes anything.
- **Go back to the previous version:**
  - Release download: set `ALERTBOT_IMAGE` in `docker\compose.env` to the previous version tag (e.g. `…:1.1.0`) and run `start.bat`.
  - Source folder: `git checkout <previous tag>`, then `update.bat`.
  - An older version can't run on a database that a newer one has already updated. Restore the backup made before the update first (below).
- **Restore a backup** with `restore.bat`.

### Restoring a backup (`restore.bat`)
1. Copies every backup inside Docker into the `backups` folder (existing files are kept), and lists them all, newest first.
2. You type the number of the backup to put back, and confirm.
3. Backs up the current data first (`db-before-restore-*`), so a restore can itself be undone with `restore.bat`.
4. Stops the bot and puts the backup back (`manage.py restore_db`). The backup is checked first: it must open, pass SQLite's integrity check and be this bot's database; otherwise nothing changes. The old database's journal files are removed so they can't be replayed onto the restored file.
5. Starts the bot. If the backup comes from an older version, it is migrated on start (with a backup first, as always).

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
