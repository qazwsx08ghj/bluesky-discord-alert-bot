# Key decisions

A quick reference to the main product and architecture decisions: what was decided, why, and what it costs. How each one works is in the linked documents; the original reasoning is in [history/](history/README.md).

## Product

**1. Only new posts are sent; existing posts are skipped when an account is added.**
- *Why:* adding an account must not flood a Discord channel with its back catalogue.
- *Tradeoff:* nothing appears until the account posts again, which can look like a fault. The panel explains it.
- *Details:* [ACCOUNTS_AND_CHECKS.md](ACCOUNTS_AND_CHECKS.md).

**2. Posts and reposts are sent by default, replies only when switched on, per account.**
- *Why:* replies are often conversation, not announcements.
- *Tradeoff:* with replies off, only the first post of a thread is sent. Switching a type on later never sends old items.
- *Details:* [ACCOUNTS_AND_CHECKS.md](ACCOUNTS_AND_CHECKS.md); decision 1 in [history/API_REVIEW.md](history/API_REVIEW.md), section 7.

**3. Named Discord channels with one default; each account follows the default or uses its own channel.**
- *Why:* one place to change a webhook, and accounts can be grouped by channel.
- *Tradeoff:* deleting a channel that's in use, or the default, needs a reassignment first. Nothing is ever left without a destination silently.
- *Details:* [CHANNELS.md](CHANNELS.md).

**4. Activity history is kept for 30 days, and at most 10,000 events.**
- *Why:* enough to understand recent problems, bounded on disk.
- *Tradeoff:* older history is gone; backups are the long-term record.
- *Details:* [ACTIVITY_AND_STATUS.md](ACTIVITY_AND_STATUS.md); decision 2 in [history/API_REVIEW.md](history/API_REVIEW.md), section 7.

**5. Failed sends are retried, but not forever: 3 more tries on later checks, within 24 hours.**
- *Why:* Discord or the network fail briefly; endless retries would hide a broken webhook.
- *Tradeoff:* a post that still fails after that is given up, and logged as a problem.
- *Details:* [BACKEND.md](BACKEND.md); decision 3 in [history/API_REVIEW.md](history/API_REVIEW.md), section 7.

## Architecture

**6. Polling Bluesky on an interval, not a live stream.**
- *How:* each account's feed is read every few minutes (default 5, 1–60), in pages of 30, until a known post or 100 items.
- *Why:* simple, needs no public address or extra service, and works on a home computer.
- *Tradeoff:* posts arrive up to one interval late. After long downtime, posts beyond those 100 items are missed; this is accepted, and there is no catch-up.
- *Details:* `bluesky_monitor/poller.py`.

**7. A post is claimed before it's sent, once per account.**
- *Why:* a post must never be sent twice, even with two checks running at once or after a restart. The database enforces one record per (account, post).
- *Tradeoff:* if the process dies mid-send, the post waits about 2 minutes before it's sent again. A duplicate is possible in exactly one case: the process dies after Discord accepted the message but before it was marked sent. Even then, it can happen only once.
- *Details:* [BACKEND.md](BACKEND.md), section 1.

**8. One checker at a time, coordinated through the database; SQLite in WAL mode; no Celery or Redis.**
- *How:* a lease in the database makes a second checker stand by. Check All runs as a background job in the same checker.
- *Why:* one small install on one computer, with nothing extra to run or configure.
- *Tradeoff:* not designed to scale across machines. It doesn't need to.
- *Details:* [BACKEND.md](BACKEND.md), section 1; [history/API_REVIEW.md](history/API_REVIEW.md) (R6).

**9. One shared Bluesky login session, reused and refreshed, instead of logging in for every account.**
- *Why:* Bluesky limits how often an account may log in.
- *Tradeoff:* a rejected App Password stops all checks until it's fixed. The panel names the fix.
- *Details:* `bluesky_monitor/bluesky.py`; problem B3 in [history/API_REVIEW.md](history/API_REVIEW.md).

**10. Settings live in the database and apply at once; `.env` is only read, once, to import an earlier install.**
- *Why:* the panel can change everything without a restart, and there's only one place where settings are kept.
- *Tradeoff:* going back to an older version means restoring a backup.
- *Details:* [SETTINGS.md](SETTINGS.md).

**11. The control panel has no login and accepts connections only from this computer (127.0.0.1).**
- *How:* changes need a CSRF token, a same-site Origin and JSON bodies, and host names are checked.
- *Why:* no password to forget, for a tool that runs on one person's computer.
- *Tradeoff:* it must not be exposed to a network; the panel isn't built for that.
- *Details:* [API.md](API.md), section 2; [SECURITY.md](../SECURITY.md).

**12. Secrets are write-only and masked: never returned by the API, and scrubbed from logs and the activity log.**
- *Why:* a webhook URL works like a password.
- *Tradeoff:* the panel can't show a saved URL. You edit it by pasting a new one.
- *Details:* [CHANNELS.md](CHANNELS.md), [ACTIVITY_AND_STATUS.md](ACTIVITY_AND_STATUS.md), "Secrets".

## Delivery and development

**13. Users install only Docker Desktop and double-click scripts. All data is in one Docker volume that the scripts never delete, and every update backs up first.**
- *Why:* no Python, Node.js or commands for non-technical owners, and no lost data.
- *Tradeoff:* it depends on Docker Desktop, which has its own license terms for larger organisations, and has Windows-specific scripts.
- *Details:* [DOCKER.md](DOCKER.md).

**14. Django serves the built React panel; there's no separate web server, and no Node.js in the running image.**
- *Why:* one service for the panel and the API, on one local port. The panel is static files.
- *Tradeoff:* none that matters for one local user.
- *Details:* [FRONTEND.md](FRONTEND.md), section 2.

**15. The API contract is generated from the code. The panel's types are generated from the contract, and real answers are tested against it.**
- *Why:* the frontend and backend can be developed separately without drifting apart. The generator is a development tool only, and the views aren't changed for it.
- *Tradeoff:* changing the API means regenerating two files and keeping the contract's descriptions accurate.
- *Details:* [CONTRIBUTING.md](../CONTRIBUTING.md), section 4; [TESTING.md](TESTING.md).

**16. MIT License, with third-party notices generated from the installed packages.**
- *Why:* permissive and simple. Generated notices can't silently go out of date.
- *Tradeoff:* changing a dependency means regenerating the notices; the checks enforce it.
- *Details:* [CONTRIBUTING.md](../CONTRIBUTING.md), section 8.

**17. Human-directed, AI-assisted development, verified by automated tests and CI.**
- *Why:* fast, careful progress, with the maintainer deciding and approving.
- *Tradeoff:* not every line has been read by a human; the tests and reviews are the safeguard.
- *Details:* [CONTRIBUTING.md](../CONTRIBUTING.md), section 10; [TESTING.md](TESTING.md).
