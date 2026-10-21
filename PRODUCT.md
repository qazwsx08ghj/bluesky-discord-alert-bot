# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

In place since step 9 (the previous single-template page, `templates/index.html`, stays at `/classic/` as a fallback for now):

- **Frontend:** React, kept logically separate from the backend.
- **Backend:** Django + Django REST Framework. Django exposes REST APIs only, shaped so a future React Native client can reuse them.
- **Packaging:** Docker, with a multi-stage build that compiles the React frontend and installs Python dependencies. Node.js and Python live only inside containers.
- **Deployment target:** Windows with Docker Desktop and Docker Compose. Users install Docker Desktop and nothing else.
- **Start command:** `setup.bat` / `start.bat` (Docker Compose underneath); users never type Docker commands.
- **Persistence:** Docker volumes keep the database and configuration (credentials, webhook, poll interval) across container restarts.
- Setup must work on Windows without anyone configuring a development environment by hand.

## Users

Non-technical Discord server owners and moderators on Windows. They follow the README step by step, have never edited code, and may not know what a terminal, webhook or app password is until the guide explains it. They set the bot up once, then return occasionally to add, pause or reroute watched accounts.

## Product Purpose

The Bluesky → Discord Alert Bot watches any public Bluesky accounts the owner picks (artists, news outlets, creators) and posts each new post into a Discord channel as an announcement embed linking back to the post. The control panel is the only place the owner configures it.

Success on the control panel, in priority order:

1. **First-time setup and day-to-day account management are equally important.** Connecting the Bluesky login and Discord webhook and getting a test message through should be free of confusion; adding, pausing, checking and routing accounts should be quick.
2. **Seeing that it works**, where possible: the bot is running, when it last checked, what was recently sent, and any errors.

## Positioning

A self-hosted, no-code bridge from Bluesky to Discord: the owner runs it on their own Windows machine, watches accounts they don't own, and manages everything from a local web page instead of config files or bot commands.

## Operating Context

- Runs locally; the control panel is opened at `http://127.0.0.1:8000/` while the bot is running. Closing or stopping the bot stops alerts.
- Setup flow the owner goes through: create a Bluesky **App Password** on their own account (the "login key", separate from the watched accounts), create a **Discord webhook** in channel settings, enter both in Settings, send a test message, then add accounts to watch.
- Polling runs on an interval (default 5 minutes, configurable) via APScheduler.
- The first check of a newly added account silently records recent posts without announcing them, so Discord is not flooded. Owners can find this confusing ("nothing appeared"), and the UI should explain it.
- Each watched account can override the default webhook to post to a different Discord channel.

## Capabilities and Constraints

Confirmed functionality (from code):

- Watched accounts: add (handle, optional display name, optional per-account webhook), list, pause/resume, remove, "Check Now" per account, "Check All Now". (`/api/accounts/`)
- Webhooks model with a single default. (`/api/webhooks/`)
- Settings: Bluesky handle, app password (write-only; the API reports only whether one is set), default webhook URL, poll interval. (`/api/settings/`)
- Send a test message to Discord. (`/api/settings/test-discord/`)
- Discord embeds use Bluesky blue `0x0085FF` and link to the post on bsky.app.

Constraints:

- Secrets (app password, webhook URLs, Django `SECRET_KEY`) must never be echoed back or exposed beyond the local machine.
- Terminology to keep consistent with the README: "Watched Accounts", "Settings", "App Password", "Webhook URL", "Check Now", "Pause / Resume", "Custom Discord channel for this account".
- Copy is written for non-technical readers: plain language, explain why a step is needed.

Open decisions:

- How the bot reports running status, last check time, recent sends and errors (the backend does not yet expose these; part of goal 2 above).
- README currently has duplicated and contradictory steps (`.env` editing vs. the Settings tab, setup.bat ordering) and will need rewriting for the Docker flow.

## Brand Commitments

- Product name: **Bluesky → Discord Alert Bot**.
- Voice: friendly, patient, step-by-step, no jargon without an explanation (as in the README).
- Visual direction (chosen 2026-10-21): the category standard, a clean admin dashboard played straight. Quality bar: Linear, the Stripe dashboard, and Discord's own settings screens.

## Evidence on Hand

- `README.md`: full setup guide and troubleshooting, the canonical source for user-facing wording.
- `templates/index.html`: current control panel (tabs for Watched Accounts and Settings).
- No screenshots, testimonials, user counts or public listings exist. Do not invent them.

## Product Principles

1. **Nobody should need a terminal after setup.** Every routine task happens in the control panel or a one-click script.
2. **Explain before asking.** Each credential or field says what it is, where to get it, and why the bot needs it.
3. **Prove it's connected.** Give the owner a way to confirm each piece works (test message, check now, status) instead of hoping.
4. **Safe with secrets.** Credentials are write-only in the UI and stay on the owner's machine.
5. **API first.** The UI only talks to Django through REST, so a future mobile client gets the same capabilities.
