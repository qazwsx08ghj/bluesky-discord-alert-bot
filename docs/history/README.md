# Project history (archive)

These documents record how the bot got to its current form: the original plan, the step-by-step changes, and the design prototype. **They describe the project as it was at the time, not as it is now.** They're kept because they explain why things are the way they are. For the current state, start with [CONTRIBUTING.md](../../CONTRIBUTING.md); the main decisions are summarised in [DECISIONS.md](../DECISIONS.md).

| Document | What it is |
|---|---|
| [API_REVIEW.md](API_REVIEW.md) | The review of the pre-release code and the plan for steps 3–10 (2026-10-21): problems found, requirements, the confirmed decisions, and the record of each step. |
| [API_CHANGES_BEFORE_1.0.md](API_CHANGES_BEFORE_1.0.md) | API changes made during development (steps 7, 9 and 10), before the first release. |
| [MIGRATIONS.md](MIGRATIONS.md) | How each step's database changes carried existing installs forward, and how they could be undone. |
| [prototype/control-panel.html](prototype/control-panel.html) | The static control-panel mock-up the design was approved from, before the React panel was built. |

The project's development steps, for reference: 1 committed database migrations for the original bot; 2 a single checker across processes, claim-before-send with retries, and a shared Bluesky session; 3 settings in the database; 4 named Discord channels; 5 activity log and status; 6 profile lookup, login test, Check All; 7 security hardening and background jobs; 8 Docker and the Windows scripts; 9 the React control panel; 10 removing the legacy layers. After step 10 came the API contract, CI and the open-source preparation.
