---
version: 1
slug: "frontend-src"
primary_target: "frontend/src"
related_targets: ["DESIGN.md", "frontend/src/styles/app.css"]
---

# Control panel

Mode: Operate. Local control panel for the Bluesky → Discord Alert Bot. Audience, job and constraints live in PRODUCT.md; the visual system lives in DESIGN.md.

## Scope

Sidebar shell with Accounts, Activity and Settings, plus a first-run Setup checklist: Bluesky login, default Discord channel, test message, first account. A global status indicator shows on every screen. Settings apply immediately, with no restart step. Local only (127.0.0.1), no login. Light and dark follow the OS, with an override in Settings → Appearance. English only for now.

## Memorable moment

Adding an account: typing a handle resolves the Bluesky profile (avatar, name) before you commit. The first check then runs by itself, and the row explains that old posts are skipped.

## Unresolved

Nothing from the original brief. Named channels, the activity log, profile lookup and the login test are all built. Possible later: translating the panel; a mobile client using the same API.

## History

This brief originally targeted the static prototype (now docs/history/prototype/control-panel.html), from which the design was approved on 2026-10-21. The shipped React panel in frontend/src replaced it as the reference.

## Direction contract

THESIS: The category standard played straight: a calm admin dashboard at the craft of Linear, Stripe's dashboard and Discord settings. It refuses novelty; distinction comes from precise states and plain-language guidance.
OWN-WORLD: Neutral slate ground and white surfaces (dark: near-black slate), 1px hairline borders, 8px radii, one accent in Bluesky blue for primary actions, selection and focus (see DESIGN.md for the exact tokens); green, amber, red only for state. Inter, tabular numerals, Lucide line icons.
STORY: The owner sees at once whether the bot is running, which accounts it watches and what it last sent; problems name the fix and link to it.
FIRST VIEWPORT: 240px sidebar (name, Accounts, Activity, Settings, setup progress). Main: page title with Check all now and primary Add account top right; a summary row (Status, Last check, Posts sent, Problems); accounts table, with the recent activity panel beside it on wide screens.
FORM: canon (category standard, chosen by the user); seed:c9fc4890b597246d
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
