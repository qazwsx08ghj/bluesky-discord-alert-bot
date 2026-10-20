---
version: 1
slug: "prototype-control-panel-html"
primary_target: "prototype/control-panel.html"
related_targets: ["frontend/src"]
---

# Control panel

Mode: Operate. Local control panel for the Bluesky → Discord Alert Bot. Audience, job and constraints live in PRODUCT.md.

## Scope

Sidebar shell with Accounts, Activity, Settings, plus a first-run Setup checklist (Bluesky login, default Discord channel, test message, first account). Global status indicator on every screen. Settings apply immediately; no restart step. Local only (127.0.0.1), no login. Light and dark follow the OS.

## Memorable moment

Adding an account: typing a handle resolves the Bluesky profile (avatar, name) before you commit, then the first check runs by itself and the row explains that old posts are skipped.

## Unresolved

Named-channel data model (DiscordWebhook vs per-account URL), event log storage, profile lookup and login test endpoints: decided in the API review.

## Direction contract

THESIS: The category standard played straight: a calm admin dashboard at the craft of Linear, Stripe's dashboard and Discord settings. It refuses novelty; distinction comes from precise states and plain-language guidance.
OWN-WORLD: Neutral slate ground and white surfaces (dark: near-black slate), 1px hairline borders, 8px radii, one accent in Bluesky blue #0085FF for primary actions, selection and focus; green, amber, red only for state. Inter-class sans, tabular numerals, Lucide-style 1.5px line icons.
STORY: The owner sees at once whether the bot is running, which accounts it watches and what it last sent; problems name the fix and link to it.
FIRST VIEWPORT: 240px sidebar (name, Accounts, Activity, Settings, setup progress). Main: page title with Check all now and primary Add account top right; a summary row (Status, Last check, Posts sent today, Problems); accounts table left, recent activity panel right.
FORM: canon (category standard, chosen by the user); seed:c9fc4890b597246d
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
