# Design: Bluesky → Discord Alert Bot control panel

The control panel (`frontend/`) is an operating tool: people come to set the bot up, then to check that it works and to manage accounts. The design is the category standard played straight, a calm admin dashboard. It stands out through precise states and plain-language guidance, not through novelty. The approved reference is `prototype/control-panel.html`; the implementation is `frontend/src/styles/app.css`.

## Colour

Tokens live on `:root`, with a dark set under `prefers-color-scheme: dark`, and both again under `[data-theme]` (Settings → Appearance).

| Role | Light | Dark |
|---|---|---|
| Background / surface / raised | `#F6F7F9` / `#FFFFFF` / `#F1F3F6` | `#0E1116` / `#151A21` / `#1B212A` |
| Text / secondary / tertiary | `#111827` / `#4B5563` / `#646B76` | `#E6E9EE` / `#B0B8C4` / `#8F98A5` |
| Borders | `#E3E6EB`, strong `#CDD2D9` | `#262D38`, strong `#343D4B` |
| Accent (primary actions, selection, focus) | `#0070E0` (brand mark `#0085FF`) | `#1570DA` |
| Link | `#0062C4` | `#69B1FF` |
| Status: ok / warning / problem / idle | `#126B41` / `#8A3B0C` / `#B42318` / `#4B5563`, each on a tinted background | `#5BD08F` / `#F5C04E` / `#FF8A80` / `#B0B8C4` |

- One accent only. Green, amber and red mean status and are used for nothing else.
- Every text and background pair is at least 4.5:1, including the white initials on the eight avatar colours (5.2:1 or more).

## Type, icons and shape

- **Type:** Inter (variable), bundled with the panel. 14 px body; 22 px page titles (600); 15 px section titles. Tabular figures for times and counts.
- **Icons:** Lucide, 16 px, 1.75 stroke. Never emoji.
- **Shape:** 1 px hairline borders, 8 px radius on controls, 12 px on panels, and soft two-layer shadows.

## Layout

- A 240 px sidebar: Setup (until done), Accounts, Activity, Settings, and the bot status at the bottom.
- A content column up to 1240 px wide (1600 px on wide screens).
- **Accounts:** a summary row (status, last check, posts sent, problems), then the accounts table and the recent activity panel, side by side from 1600 px.
- **Smaller windows:**
  - below 1180 px, each account row stacks and its actions become icon buttons with names;
  - below 860 px, the sidebar becomes a top bar;
  - below 560 px, the summary is one column.

## Components and states

- **Buttons:** primary (accent), default (outlined), ghost and danger. While working, a button shows a spinner and says what it's doing ("Saving…").
- **Badges:** Active, Paused, Checking now, Waiting for first check (with the reason underneath), Problem.
- **Banners:** one per screen, for the most important current problem. They name the problem, explain it, and offer the fix as a button.
- **Drawer** for adding or editing an account; **modal** for confirmations and channel forms. Both are native `<dialog>`: focus stays inside, Escape closes, and focus returns to the opener.
- **Toasts:** what just happened, in one sentence. Problems are announced assertively and stay longer.
- **Lists:** every list has loading (skeleton), empty (what it's for, and the next action) and error (the message, and Try again) states.
- **Offline:** a banner explains what to do (Docker Desktop, `start.bat`), the page dims, and it reconnects by itself.

## Words

Plain language, for people who've never edited code. Every field says what it is, where to get it, and why the bot needs it. The README's terms are used everywhere: App Password, Webhook URL, Check now, Check all now, Pause and Resume, default channel. Errors name the problem and the way out.

## Motion

Short ease-out entrances for menus, drawers, dialogs and toasts, a highlight on a newly added row, and pulsing dots for work in progress. Everything respects "reduce motion".
