# The control panel (React, step 9)

The control panel is a React app in `frontend/`. It talks to Django only through the REST API documented in [API.md](API.md). Users never build it: the Docker image builds it, and Django serves the result at http://127.0.0.1:8000/.

![Accounts](screenshots/02-accounts.png)

## 1. What's where

```
frontend/
  index.html               the one HTML page (no inline scripts or styles)
  src/
    main.tsx               start-up: font, styles, theme, <App/>
    App.tsx                routes and providers (data, toasts, the account drawer)
    api/client.ts          every request: same site only, JSON, CSRF token, plain-language errors
    api/queries.ts         what each screen reads, how often it refreshes, what it can change
    api/types.ts           the API's shapes
    components/Shell.tsx   sidebar, bot status, "can't reach the bot" banner, focus on page change
    components/ui.tsx      buttons, badges, banners, native <dialog> modals, menu, toasts, skeletons
    features/              the account drawer, Check All, channels, login form, activity entries, status
    pages/                 Setup, Accounts, Activity, Settings
    lib/                   times, avatars, theme, setup progress, problem fixes
    styles/app.css         the whole design: tokens (light and dark), layout, components
    test/                  integration tests with a fake API (Vitest, Testing Library, MSW)
  e2e/                     browser tests (Playwright) and the screenshots in docs/screenshots/
  vite.config.ts           build and dev server
  playwright.config.ts     browser tests
```

| Address | Screen |
|---|---|
| `/` | Opens Setup on a fresh install, Accounts afterwards |
| `/setup` | First-run steps: Bluesky login, Discord channel, test message, first account. Hidden once setup is complete. |
| `/accounts` | Summary, watched accounts, Check all, recent activity |
| `/activity` | The activity log, filtered by All, Sent or Problems |
| `/settings` | Bluesky login, Discord channels, check frequency, appearance, about |

## 2. How Django serves it

`config/frontend.py`:
- **The page:** `/`, `/setup`, `/accounts`, `/activity` and `/settings` (with or without a trailing slash) return the built `index.html`. React then shows the right screen. Any other address is a 404.
- **Its files:** `/assets/<name>` serves only names that look like built files, only from `frontend/dist/assets`, with explicit content types. `/favicon.svg` serves the icon. Nothing else on disk can be reached: tests try `..`, encoded slashes, `.env`, database names and other file types.
- **The CSRF cookie:** the page sets the `csrftoken` cookie (`SameSite=Strict`). `api/client.ts` sends it back in `X-CSRFToken` on every change, and fetches a fresh one once if the server refuses it.
- **Headers:** a strict Content Security Policy:
  - `script-src 'self'; style-src 'self'; connect-src 'self'`: no inline code, and calls only to this site;
  - `img-src 'self' data: https://cdn.bsky.app`: avatars from Bluesky;
  - `frame-ancestors 'none'; object-src 'none'; base-uri 'none'`.

  Also `X-Frame-Options: DENY`. The page is `Cache-Control: no-cache`, so a new version appears on the next load. The files are cached for good, because their names change with every build.
- **Not built?** Only possible in a source checkout run without Docker: `/` then shows a short page explaining `npm ci` and `npm run build`. The API works either way. (The previous page, kept at `/classic/` during step 9, was removed in step 10.)
- **Where the build is:** `FRONTEND_DIST` (default `frontend/dist`). In Docker, the image's `panel` stage builds it (see the `Dockerfile`).

The API is unchanged: the panel uses exactly the routes and fields in API.md, so a future React Native app can do everything the panel does.

## 3. Design

The approved prototype (`prototype/control-panel.html`) is the reference; `src/styles/app.css` carries it over:
- **Look:** neutral slate background, white surfaces, 1px borders and 8px corners. One Bluesky-blue accent for primary actions, selection and focus. Green, amber and red only for status.
- **Type and icons:** Inter, bundled with the panel, so no font is fetched from the internet. Lucide icons.
- **Light and dark:**
  - colour tokens on `:root`, the dark set under `prefers-color-scheme`, and both again under `[data-theme]`;
  - Settings → Appearance overrides the computer's setting, and the choice is kept in this browser.
- **Sizes:**
  - from 1600 px wide, the recent activity panel sits beside the accounts table;
  - below 1180 px, each account row stacks, so its buttons stay in view;
  - below 860 px, the sidebar becomes a top bar.

  Tested at 1440×900, 1024×768 and 768×1024.
- **Keyboard and screen readers:**
  - a skip link;
  - focus moves to the page after navigation;
  - the drawers and dialogs are native modal `<dialog>`s: focus stays inside, Escape closes them, and focus returns to what opened them;
  - the actions menu works with the arrow keys, Home, End and Escape;
  - every control has a visible label or an accessible name;
  - status messages are announced through live regions;
  - motion respects "reduce motion".
- **States:** every list has loading, empty and error states. Every action shows progress while it runs, then a message saying what happened.
- **When the bot can't be reached:** a plain-language banner explains what to do, the page dims, and it reconnects by itself.

## 4. Working on it

You need Node.js 20.19 or newer (22 recommended). Users never do.

```
cd frontend
npm ci                          # exact versions from package-lock.json
npm run dev                     # http://localhost:5173, using the API of the bot on port 8000
```

`npm run dev` passes `/api` to `ALERTBOT_API` (default `http://127.0.0.1:8000`). Without a running bot, start the demo server:
- a temporary database;
- made-up accounts;
- fake Bluesky and Discord;
- no network access;
- it never reads or writes your `.env`.

```
venv\Scripts\python config\testing\demo_server.py --port 8765 --scenario working   # or empty, problem
set ALERTBOT_API=http://127.0.0.1:8765
npm run dev
```

The demo server already trusts `http://localhost:5173` for changes. Against a real bot, set `CSRF_TRUSTED_ORIGINS=http://localhost:5173` for it (see API.md, "Security").

`npm run build` type-checks and writes `frontend/dist`; Django serves it at once, with no restart. `update.bat` rebuilds the Docker image, panel included.

## 5. Tests

| Command (in `frontend/`) | What it covers | Time |
|---|---|---|
| `npm test` | 50 integration tests. The whole app in a simulated browser against a fake API that follows API.md, CSRF rules included. Covers setup, accounts, drawer, Check All, settings, channels, offline, CSRF retry, secrets, keyboard and activity. | ~15 s |
| `npm run e2e` | Builds the panel, starts the demo server and runs 14 Chromium tests against the real Django app and checker. Covers the setup-to-first-account flow, Check now sending a post, Check All surviving a reload, problems and their fixes, keyboard-only use, tablet sizes, addresses, the CSP, and no secrets in pages or API answers. | ~1 min |
| `npm run screenshots` | Writes `docs/screenshots/` from the demo data | ~30 s |
| `RUN_DOCKER_TESTS=1` with `manage.py test config.tests_docker` | Also runs the `@docker` browser tests against the real containers | ~5 min |

The first browser run needs `npx playwright install chromium`. Django's own tests (`manage.py test`) check the serving, the headers and the file rules with a temporary build, so they don't need Node.js.

**Isolation:** the browser tests and screenshots use only the demo server. Its settings are set before Django starts:
- empty credentials;
- a temporary database;
- the `.env` copy turned off;
- connections leaving the computer refused.

## 6. Dependencies

All versions are exact and locked (`package-lock.json`): React 19, React Router 7, TanStack Query 5, Lucide, Inter, plus Vite, TypeScript, Vitest, Testing Library, MSW and Playwright for building and testing. The image runs `npm ci --ignore-scripts`, so no package runs code while it installs. Only the built files go into the image: no Node.js, no `node_modules`.
