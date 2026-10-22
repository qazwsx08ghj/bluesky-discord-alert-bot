# Product

<!-- impeccable:product-schema 1 -->

> Internal product brief for maintainers and the design tooling (it pairs with [DESIGN.md](DESIGN.md)). Users should read the [README](README.md); developers, [CONTRIBUTING.md](CONTRIBUTING.md).

## Platform

web

## Stack

- **Frontend:** a React control panel (`frontend/`), built into static files and served by Django. It talks to Django only through the REST API.
- **Backend:** Django + Django REST Framework, plus a background checker (APScheduler) as a separate service. The REST API is shaped so a future React Native client can reuse it, and it's described by an OpenAPI contract (`docs/openapi.yaml`).
- **Packaging:** one Docker image. A multi-stage build compiles the panel with Node.js; the image that runs has no Node.js.
- **Deployment target:** Windows with Docker Desktop. Users install Docker Desktop and nothing else.
- **Start and manage:** `setup.bat`, `start.bat`, `stop.bat`, `update.bat`, `backup.bat` and `restore.bat` (Docker Compose underneath). Users never type commands.
- **Persistence:** one Docker volume holds the SQLite database (accounts, channels, settings, history), the backups and the generated secret key. Updates and restarts keep it.
- **Releases:** a prebuilt image and a small download (the scripts plus a Compose file), so nothing is built on users' computers.

## Users

Non-technical Discord server owners and moderators on Windows. They follow the README step by step, have never edited code, and may not know what a terminal, webhook or App Password is until the guide explains it. They set the bot up once, then come back now and then to add, pause or reroute accounts, or to fix a problem the panel points out.

## Product Purpose

The Bluesky → Discord Alert Bot watches any public Bluesky accounts the owner picks (artists, news outlets, creators) and sends each new post to a Discord channel as an embed linking back to the post. The control panel is the only place the owner configures it.

Success on the control panel, in priority order:

1. **First-time setup and day-to-day account management are equally important.**
   - Connecting the Bluesky login and a Discord channel, then getting a test message through, should be free of confusion.
   - Adding, pausing, checking and routing accounts should be quick.
2. **Seeing that it works:**
   - whether the bot is running, and when it last checked;
   - what was recently sent;
   - any problem, with its fix.

## Positioning

A self-hosted, no-code bridge from Bluesky to Discord. The owner runs it on their own Windows computer, follows accounts they don't own, and manages everything from a local web page instead of config files or bot commands. It's free and open source (MIT).

## Operating Context

- **Local only:** the panel is at `http://127.0.0.1:8000/` and has no login, so it only accepts connections from this computer. The bot runs while Docker Desktop runs.
- **First run:** the panel's Setup walks through four steps:
  1. connect the owner's own Bluesky login with an **App Password** (tested before saving);
  2. add a Discord channel with its **Webhook URL**, which becomes the default channel;
  3. send a test message;
  4. add the first account.
- **Checking:** on an interval (default 5 minutes, 1–60), by one checker. **Check now** and **Check all now** are available at any time; Check all runs in the background.
- **The first check** of a new account records its existing posts without sending them, so Discord isn't flooded. Owners can find this confusing ("nothing appeared"), and the panel explains it.
- **Routing:** each account sends either to the default channel or to a named channel picked for it. It sends posts and reposts by default, and replies only if switched on.
- **Downtime:** long downtime can miss posts (polling, not catch-up). This is accepted (see [docs/DECISIONS.md](docs/DECISIONS.md)).

## Capabilities and Constraints

Confirmed functionality (from code):

- **Accounts:** add with a Bluesky profile lookup, edit, pause and resume, remove, per-account post types (posts, reposts, replies) and channel, Check now, Check all now. (`/api/accounts/`, `/api/bluesky/profile/`, `/api/check-jobs/`)
- **Named Discord channels:** one default, a test message per channel, and deletion that never strands an account. (`/api/webhooks/`)
- **Settings:**
  - the Bluesky handle;
  - the App Password, which is write-only (the API reports only whether one is set);
  - a login test;
  - the check interval.

  (`/api/settings/`)
- **Status and activity:** bot status, an activity log, and problems with a plain-language fix. (`/api/status/`, `/api/activity/`)
- **Data safety:** automatic backups before every update and before database migrations; `backup.bat` and `restore.bat`.
- **Discord embeds:** Bluesky blue `0x0085FF`, titled "New post from X", "X reposted" or "New reply from X", linking to the post on bsky.app.

Constraints:

- **Secrets stay secret:** the App Password, webhook URLs and Django's secret key are never echoed back or exposed beyond the local machine. Webhook URLs are shown masked.
- **Terminology:**
  - **The panel uses:** "Setup", "Accounts" (and "Watched accounts" as the table heading), "Activity", "Settings", "App Password", "Webhook URL", "default channel", "Check now", "Check all now", "Pause" / "Resume". It says "announce" for sending posts to Discord ("Where announcements go").
  - **The documentation says** "notification" in English and 「通知」 in Traditional Chinese.
  - Keep each consistent within its own context, and align them if the panel's copy is revised.
- **Copy** is written for non-technical readers: plain language, and it explains why a step is needed.
- **Languages:** the panel is in English only for now. The user documentation is in English and Traditional Chinese (Taiwan).

Open decisions:

- Translating the panel (internationalisation) is a possible future feature.
- A mobile client is possible later; the API is ready for it, but nothing is planned.

## Brand Commitments

- Product name: **Bluesky → Discord Alert Bot**.
- Voice: friendly, patient, step-by-step, no jargon without an explanation (as in the README).
- Visual direction (chosen 2026-10-21): the category standard, a clean admin dashboard played straight. Quality bar: Linear, the Stripe dashboard, and Discord's own settings screens.

## Evidence on Hand

- **`README.md` / `README.zh-TW.md`:** the setup guide and troubleshooting, the canonical source for user-facing wording.
- **`frontend/`:** the shipped control panel. `docs/screenshots/` are taken from it with made-up demo data.
- **What doesn't exist:** no testimonials, user counts or public listings. Don't invent them.

## Product Principles

1. **Nobody should need a terminal after setup.** Every routine task happens in the control panel or a one-click script.
2. **Explain before asking.** Each credential or field says what it is, where to get it, and why the bot needs it.
3. **Prove it's connected.** Give the owner a way to confirm each piece works (login test, test message, check now, status) instead of hoping.
4. **Safe with secrets.** Credentials are write-only in the UI and stay on the owner's machine.
5. **API first.** The UI only talks to Django through REST, so a future mobile client gets the same capabilities.
