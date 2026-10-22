// An in-memory stand-in for the Django API, for the frontend integration tests.
// It follows docs/API.md: the same shapes, status codes and rules (CSRF token required for
// changes, masked webhook URLs, write-only App Password, channel delete rules, background
// Check All jobs). Nothing here talks to a real server, Bluesky or Discord.
import { http, HttpResponse, type HttpHandler } from "msw";
import type { components } from "../api/schema.gen";
import type { Account, ActivityEvent, BotStatus, Channel, CheckJob, CursorPage, LoginTest, PollResult, ProfileLookup, Settings } from "../api/types";

type Schemas = components["schemas"];
/** Form errors per field, e.g. {handle: ["…"]} (the contract's 400 for accounts and channels). */
type FieldErrors = Record<string, string[]>;

export const TOKEN = "test-csrf-token";
const WEBHOOK = /^https:\/\/(?:(?:ptb|canary)\.)?discord(?:app)?\.com\/api(?:\/v\d+)?\/webhooks\/\d+\/[A-Za-z0-9_-]+\/?$/;

const PROFILES: Record<string, string> = {
  "mira-draws.bsky.social": "Mira Draws",
  "pixelforge.games": "Pixel Forge Games",
  "studio-kite.bsky.social": "Studio Kite",
  "greenhouse.bsky.social": "Greenhouse Weekly",
};

interface FakeDestination {
  channel_id: number | null;
  role_id: string;
}

interface FakeAccount {
  id: number;
  handle: string;
  display_name: string;
  destinations: FakeDestination[];
  is_active: boolean;
  first_poll_done: boolean;
  announce_posts: boolean;
  announce_reposts: boolean;
  announce_replies: boolean;
  last_checked_at: string | null;
  created_at: string;
}

interface FakeChannel {
  id: number;
  name: string;
  url: string;
  is_default: boolean;
  last_delivery: Channel["last_delivery"];
}

export interface FakeState {
  handle: string;
  password: string;
  loginState: "ok" | "failed" | "unknown";
  interval: number;
  channels: FakeChannel[];
  accounts: FakeAccount[];
  events: ActivityEvent[];
  jobs: CheckJob[];
  discord: { channel: string; text: string }[];
  offline: boolean;
  checkerHealthy: boolean;
  requests: { method: string; path: string; body: unknown; token: string | null }[];
  token: string;
}

const now = () => new Date().toISOString();
const minutesAgo = (n: number) => new Date(Date.now() - n * 60_000).toISOString();

export function mask(url: string) {
  const match = url.match(/webhooks\/(\d+)\/(.+)$/);
  return match ? `https://discord.com/api/webhooks/${match[1]}/****${match[2].slice(-4)}` : "";
}

export function emptyState(): FakeState {
  return {
    handle: "",
    password: "",
    loginState: "unknown",
    interval: 5,
    channels: [],
    accounts: [],
    events: [],
    jobs: [],
    discord: [],
    offline: false,
    checkerHealthy: true,
    requests: [],
    token: TOKEN,
  };
}

export const SECRET_PASSWORD = "abcd-efgh-ijkl-mnop";
export const SECRET_WEBHOOK = "https://discord.com/api/webhooks/1189000000000000001/SECRET-token-announcements";
export const SECRET_WEBHOOK_2 = "https://discord.com/api/webhooks/1204000000000000002/SECRET-token-art-drops";

export function workingState(): FakeState {
  const s = emptyState();
  s.handle = "alertbot-demo.bsky.social";
  s.password = SECRET_PASSWORD;
  s.loginState = "ok";
  s.channels = [
    { id: 1, name: "announcements", url: SECRET_WEBHOOK, is_default: true, last_delivery: { at: minutesAgo(3), ok: true, error: "" } },
    { id: 2, name: "art-drops", url: SECRET_WEBHOOK_2, is_default: false, last_delivery: null },
  ];
  const base = { announce_posts: true, announce_reposts: true, announce_replies: false, created_at: minutesAgo(600) };
  s.accounts = [
    { id: 1, handle: "mira-draws.bsky.social", display_name: "Mira Draws", destinations: [{ channel_id: 2, role_id: "" }], is_active: true, first_poll_done: true, last_checked_at: minutesAgo(1), ...base },
    { id: 2, handle: "pixelforge.games", display_name: "Pixel Forge Games", destinations: [{ channel_id: null, role_id: "" }], is_active: true, first_poll_done: true, last_checked_at: minutesAgo(2), ...base },
    { id: 3, handle: "tamsin.bsky.social", display_name: "Tamsin Okoro", destinations: [{ channel_id: null, role_id: "" }], is_active: false, first_poll_done: true, last_checked_at: minutesAgo(4000), ...base },
  ];
  s.events = [
    event(3, "post_sent", "success", "Sent a post from @mira-draws.bsky.social.", { account: s.accounts[0], channel: s.channels[1], detail: { post_url: "https://bsky.app/profile/mira-draws.bsky.social/post/1", post_text: "Commissions open for March!" } }),
    event(2, "send_failed", "warning", "Couldn't send a post from @pixelforge.games (try 1 of 4).", { account: s.accounts[1], channel: s.channels[0], problem: { category: "discord_unavailable", title: "Discord couldn't be reached", explanation: "This is usually temporary.", fix: "wait" } }),
    event(1, "checker_started", "info", "The checker started.", {}),
  ];
  return s;
}

let nextEventId = 100;
function event(id: number, type: string, level: ActivityEvent["level"], message: string, extra: { account?: FakeAccount; channel?: FakeChannel; detail?: ActivityEvent["detail"]; problem?: ActivityEvent["problem"] }): ActivityEvent {
  return {
    id,
    created_at: minutesAgo(id),
    last_seen_at: minutesAgo(id),
    repeat_count: 1,
    type,
    level,
    message,
    problem: extra.problem ?? null,
    account: extra.account ? { id: extra.account.id, label: "@" + extra.account.handle } : null,
    channel: extra.channel ? { id: extra.channel.id, label: extra.channel.name } : null,
    attempt: null,
    is_retry: false,
    detail: extra.detail ?? {},
  };
}

function toChannel(s: FakeState, c: FakeChannel): Channel {
  return {
    id: c.id,
    name: c.name,
    url_masked: mask(c.url),
    is_default: c.is_default,
    created_at: minutesAgo(1000),
    updated_at: minutesAgo(10),
    account_count: s.accounts.filter((a) => a.destinations.some((d) => d.channel_id === c.id)).length,
    last_delivery: c.last_delivery,
  };
}

/** The bot's checks of a destinations list (bluesky_monitor/serializers.py), with its messages. */
function checkDestinations(s: FakeState, list: unknown): { value?: FakeDestination[]; errors?: FieldErrors | Record<string, unknown> } {
  if (!Array.isArray(list) || list.length === 0) return { errors: { destinations: ["Choose at least one channel to send to."] } };
  if (list.length > 10) return { errors: { destinations: ["An account can send to at most 10 channels."] } };
  const rows: Record<string, string[]>[] = [];
  const value: FakeDestination[] = [];
  const seen = new Set<number | null>();
  for (const item of list as { channel_id?: number | null; role_id?: string }[]) {
    const row: Record<string, string[]> = {};
    const channelId = item.channel_id ?? null;
    if (channelId !== null && !s.channels.some((c) => c.id === channelId)) row.channel_id = [`Invalid pk "${channelId}" - object does not exist.`];
    else if (seen.has(channelId)) row.channel_id = ["This channel is already in the list."];
    seen.add(channelId);
    let role = String(item.role_id ?? "").trim();
    const pasted = /^<@&([0-9]+)>$/.exec(role);
    if (pasted) role = pasted[1];
    if (["everyone", "here"].includes(role.toLowerCase().replace(/^@/, ""))) row.role_id = ["@everyone and @here can't be mentioned. Enter the ID of a role instead, or leave this empty to send without a mention."];
    else if (role === "1100000000000000000") row.role_id = ["This is the Discord server's ID, which Discord also uses for @everyone. Enter the ID of a role instead."];
    else if (role && !/^[0-9]{17,20}$/.test(role)) row.role_id = ["A Role ID is a long number, like 123456789012345678. In Discord, turn on Developer Mode (User Settings > Advanced), then open Server Settings > Roles, click … next to the role and choose Copy Role ID."];
    rows.push(row);
    value.push({ channel_id: channelId, role_id: role });
  }
  return rows.some((r) => Object.keys(r).length) ? { errors: { destinations: rows } } : { value };
}

function toAccount(s: FakeState, a: FakeAccount): Account {
  const state = !a.is_active ? "paused" : !a.first_poll_done ? "waiting_first_check" : "active";
  const labels = { paused: "Paused", waiting_first_check: "Waiting for first check", active: "Active" } as const;
  return {
    id: a.id,
    handle: a.handle,
    display_name: a.display_name,
    is_active: a.is_active,
    destinations: a.destinations.map((d) => {
      const channel = s.channels.find((c) => c.id === d.channel_id) ?? null;
      return {
        channel_id: d.channel_id,
        role_id: d.role_id,
        follows_default: d.channel_id === null,
        channel: channel ? { id: channel.id, name: channel.name, is_default: channel.is_default } : null,
      };
    }),
    announce_posts: a.announce_posts,
    announce_reposts: a.announce_reposts,
    announce_replies: a.announce_replies,
    first_poll_done: a.first_poll_done,
    created_at: a.created_at,
    state,
    state_label: labels[state],
    state_message: "",
    checking_now: false,
    problem: null,
    first_check: { done: a.first_poll_done, at: a.first_poll_done ? minutesAgo(500) : null, seeded_posts: a.first_poll_done ? 2 : null },
    last_checked_at: a.last_checked_at,
    last_check_ok: a.last_checked_at ? true : null,
    last_success_at: a.last_checked_at,
    last_error: null,
  };
}

function login(s: FakeState) {
  if (!s.handle || !s.password) return { state: "not_configured" as const, handle: s.handle, problem: null, retry_after: null };
  return {
    state: s.loginState,
    handle: s.handle,
    problem: s.loginState === "failed" ? { category: "bluesky_login_rejected", title: "Bluesky rejected the bot's login", explanation: "Create a new App Password and save it in Settings.", fix: "update_login" as const } : null,
    retry_after: null,
  };
}

function settingsOf(s: FakeState): Settings {
  const d = s.channels.find((c) => c.is_default);
  return {
    bluesky_handle: s.handle,
    has_bluesky_password: Boolean(s.password),
    default_channel: d ? { id: d.id, name: d.name } : null,
    poll_interval_minutes: s.interval,
    settings_version: s.requests.length + 1,
    updated_at: now(),
    bluesky_login: login(s),
  };
}

export function statusOf(s: FakeState): BotStatus {
  const setup = { bluesky_login: Boolean(s.handle && s.password), discord_channel: s.channels.length > 0, accounts: s.accounts.length > 0 };
  const complete = setup.bluesky_login && setup.discord_channel && setup.accounts;
  const problems: BotStatus["problems"] =
    s.loginState === "failed" && setup.bluesky_login
      ? [{ code: "bluesky_login_rejected", category: "bluesky_login_rejected", severity: "error", title: "Bluesky rejected the bot's login", explanation: "Create a new App Password and save it in Settings.", fix: "update_login", message: "Checks are on hold until the login works.", since: minutesAgo(6) }]
      : [];
  const state = !s.checkerHealthy ? "unavailable" : !complete ? "not_configured" : problems.length ? "problem" : "running";
  return {
    state,
    headline: { running: "Running normally", problem: "Running, with problems", not_configured: "Setup isn't finished", unavailable: "The checker isn't running" }[state],
    message: state === "running" ? `Checking ${s.accounts.filter((a) => a.is_active).length} accounts every ${s.interval} minutes.` : "Setup isn't finished.",
    checked_at: now(),
    scheduler: {
      state: s.checkerHealthy ? "running" : "stopped",
      healthy: s.checkerHealthy,
      message: s.checkerHealthy ? "The checker is running." : "The checker isn't running.",
      problem: s.checkerHealthy ? null : { category: "scheduler_stopped", title: "The checker stopped", explanation: "Open Docker Desktop and run start.bat.", fix: "restart_bot" },
      heartbeat_at: now(),
      heartbeat_age_seconds: 3,
      active_since: minutesAgo(60),
      check_interval_minutes: s.interval,
      checking_now: false,
      current_check_started_at: null,
      last_check: { started_at: minutesAgo(3), finished_at: minutesAgo(2), result: "ok", message: "Every account was checked.", problem: null, accounts_checked: 2, accounts_failed: 0, posts_sent: 0 },
      next_check_at: new Date(Date.now() + 3 * 60_000).toISOString(),
    },
    bluesky_login: login(s),
    setup: { ...setup, complete, missing: [] },
    counts: {
      accounts_total: s.accounts.length,
      accounts_active: s.accounts.filter((a) => a.is_active).length,
      channels: s.channels.length,
      pending_deliveries: 0,
      deliveries_waiting: 0,
      deliveries_retrying: 0,
      sent_last_24h: s.discord.length + (complete ? 4 : 0),
      failed_sends_last_24h: 0,
    },
    problems,
    recent_errors: [],
  };
}

function page<T>(results: T[]) {
  return { count: results.length, next: null, previous: null, results };
}

function advance(s: FakeState, job: CheckJob) {
  // Every read of a running job moves it one account further, like a real check in progress.
  if (job.final) return;
  job.status = "running";
  job.status_label = "Checking";
  const row = job.accounts.find((r) => r.result === "waiting" || r.result === "checking");
  if (row) {
    if (row.result === "waiting") {
      row.result = "checking";
      row.message = "Being checked now.";
      job.progress.current = { id: row.id, handle: row.handle };
    } else {
      row.result = "checked";
      row.message = "No new posts.";
      job.progress.done += 1;
      job.counts.checked += 1;
      job.counts.waiting -= 1;
      const acc = s.accounts.find((a) => a.id === row.id);
      if (acc) acc.last_checked_at = now();
    }
    job.progress.percent = Math.round((job.progress.done / Math.max(1, job.progress.total)) * 100);
    job.message = `Checking… (${job.progress.done} of ${job.progress.total} done)`;
  }
  if (job.accounts.every((r) => !["waiting", "checking"].includes(r.result))) {
    Object.assign(job, { status: "finished", final: true, result: "ok", status_label: "Finished", finished_at: now(), message: `Checked ${job.counts.checked} accounts. No new posts.`, progress: { ...job.progress, current: null } });
  }
}

/** MSW handlers bound to one state object, which the test can inspect and change. */
export function handlersFor(s: FakeState): HttpHandler[] {
  const guard = (request: Request, body: unknown) => {
    const token = request.headers.get("X-CSRFToken");
    s.requests.push({ method: request.method, path: new URL(request.url).pathname + new URL(request.url).search, body, token });
    if (token !== s.token) return HttpResponse.json({ code: "csrf_failed", message: "This request is missing its security token, so it was refused." } satisfies Schemas["Error"], { status: 403 });
    // Like the real server: only a request that has a body must send it as JSON.
    if (body !== null && request.headers.get("Content-Type") !== "application/json") return HttpResponse.json({ code: "json_required", message: "Send JSON." } satisfies Schemas["Error"], { status: 415 });
    return null;
  };
  const read = async (request: Request) => {
    const text = await request.text();
    return text ? JSON.parse(text) : {};
  };
  const offline = () => (s.offline ? HttpResponse.error() : null);
  const record = (type: string, level: ActivityEvent["level"], message: string, extra: Parameters<typeof event>[4]) => {
    s.events.unshift({ ...event(nextEventId++, type, level, message, extra), created_at: now() });
  };

  return [
    http.get("*/api/csrf/", () => {
      document.cookie = `csrftoken=${s.token}; path=/`;
      return HttpResponse.json({ csrf_token: s.token, header_name: "X-CSRFToken", cookie_name: "csrftoken" } satisfies Schemas["Csrf"]);
    }),
    http.get("*/api/status/", () => offline() ?? HttpResponse.json(statusOf(s))),
    http.get("*/api/health/", () => offline() ?? HttpResponse.json({ status: "ok", version: "test", database: true, migrations_pending: 0, checker: "running" } satisfies Schemas["Health"])),
    http.get("*/api/settings/", () => offline() ?? HttpResponse.json(settingsOf(s))),
    http.patch("*/api/settings/", async ({ request }) => {
      const body = await read(request);
      const refused = guard(request, body);
      if (refused) return refused;
      const fields: Record<string, string[]> = {};
      if ("poll_interval_minutes" in body && !(body.poll_interval_minutes >= 1 && body.poll_interval_minutes <= 60)) fields.poll_interval_minutes = ["Choose between 1 and 60 minutes."];
      if ("bluesky_handle" in body && /\s/.test(body.bluesky_handle)) fields.bluesky_handle = ["A Bluesky handle can't contain spaces."];
      if (Object.keys(fields).length) return HttpResponse.json({ code: "invalid_settings", message: "Some settings weren't valid. Nothing was saved.", fields } satisfies Schemas["Error"], { status: 400 });
      if (body.bluesky_handle !== undefined) s.handle = body.bluesky_handle;
      if (body.bluesky_password) {
        s.password = body.bluesky_password;
        s.loginState = "unknown";
      }
      if (body.poll_interval_minutes) s.interval = body.poll_interval_minutes;
      return HttpResponse.json({ status: "saved", settings: settingsOf(s) } satisfies Schemas["SettingsSaved"]);
    }),
    http.post("*/api/settings/test-login/", async ({ request }) => {
      const body = await read(request);
      const refused = guard(request, body);
      if (refused) return refused;
      const handle = (body.bluesky_handle || s.handle || "").trim();
      const password = (body.bluesky_password || s.password || "").trim();
      if (!handle || !password) return HttpResponse.json({ ok: false, result: "invalid", code: "bluesky_not_configured", message: "Enter the bot's Bluesky handle and App Password.", saved: false } satisfies LoginTest, { status: 400 });
      if (password.includes("offline")) return HttpResponse.json({ ok: false, result: "unavailable", code: "bluesky_unavailable", message: "Couldn't reach Bluesky. Check the internet connection and try again.", problem: null, saved: false } satisfies LoginTest, { status: 503 });
      if (password.includes("wrong")) return HttpResponse.json({ ok: false, result: "rejected", code: "bluesky_login_rejected", message: "Bluesky didn't accept this handle and App Password. Check both and try again.", problem: null, saved: false } satisfies LoginTest);
      return HttpResponse.json({ ok: true, result: "ok", code: "login_ok", message: "The login works. It isn't saved yet: press Save to start using it.", problem: null, saved: false, warning: null, account: { did: "did:plc:x", handle } } satisfies LoginTest);
    }),
    http.get("*/api/webhooks/", () => offline() ?? HttpResponse.json(page(s.channels.map((c) => toChannel(s, c))))),
    http.post("*/api/webhooks/", async ({ request }) => {
      const body = await read(request);
      const refused = guard(request, body);
      if (refused) return refused;
      const errors: Record<string, string[]> = {};
      if (!body.name?.trim()) errors.name = ["Give the channel a name."];
      else if (s.channels.some((c) => c.name.toLowerCase() === body.name.trim().toLowerCase())) errors.name = ["A channel with this name already exists."];
      if (!WEBHOOK.test(body.url ?? "")) errors.url = ["Paste the full webhook URL from Discord. It starts with https://discord.com/api/webhooks/"];
      if (Object.keys(errors).length) return HttpResponse.json(errors, { status: 400 });
      const channel: FakeChannel = { id: Math.max(0, ...s.channels.map((c) => c.id)) + 1, name: body.name.trim(), url: body.url, is_default: false, last_delivery: null };
      s.channels.push(channel);
      if (body.is_default || s.channels.length === 1) s.channels.forEach((c) => (c.is_default = c.id === channel.id));
      return HttpResponse.json(toChannel(s, channel), { status: 201 });
    }),
    http.patch("*/api/webhooks/:id/", async ({ request, params }) => {
      const body = await read(request);
      const refused = guard(request, body);
      if (refused) return refused;
      const channel = s.channels.find((c) => c.id === Number(params.id));
      if (!channel) return HttpResponse.json({ code: "not_found", message: "Not found." } satisfies Schemas["Error"], { status: 404 });
      if (body.url && !body.url.includes("****") && !WEBHOOK.test(body.url)) return HttpResponse.json({ url: ["Paste the full webhook URL from Discord. It starts with https://discord.com/api/webhooks/"] } satisfies FieldErrors, { status: 400 });
      if (body.name) channel.name = body.name;
      if (body.url && !body.url.includes("****")) channel.url = body.url;
      if (body.is_default) s.channels.forEach((c) => (c.is_default = c.id === channel.id));
      return HttpResponse.json(toChannel(s, channel));
    }),
    http.post("*/api/webhooks/:id/make_default/", async ({ request, params }) => {
      const refused = guard(request, {});
      if (refused) return refused;
      s.channels.forEach((c) => (c.is_default = c.id === Number(params.id)));
      return HttpResponse.json(toChannel(s, s.channels.find((c) => c.id === Number(params.id))!));
    }),
    http.post("*/api/webhooks/:id/test/", async ({ request, params }) => {
      const refused = guard(request, {});
      if (refused) return refused;
      const channel = s.channels.find((c) => c.id === Number(params.id))!;
      const ok = !channel.url.includes("broken");
      channel.last_delivery = { at: now(), ok, error: ok ? "" : "The test message wasn't accepted by Discord." };
      if (!ok) return HttpResponse.json({ status: "failed" } satisfies Schemas["Sent"], { status: 502 });
      s.discord.push({ channel: channel.name, text: "test" });
      return HttpResponse.json({ status: "sent" } satisfies Schemas["Sent"]);
    }),
    http.delete("*/api/webhooks/:id/", ({ request, params }) => {
      const refused = guard(request, null);
      if (refused) return refused;
      const channel = s.channels.find((c) => c.id === Number(params.id))!;
      const reassign = new URL(request.url).searchParams.get("reassign_to");
      if (channel.is_default) return HttpResponse.json({ code: "channel_is_default", message: "This is the default channel." } satisfies Schemas["Error"], { status: 409 });
      const users = s.accounts.filter((a) => a.destinations.some((d) => d.channel_id === channel.id));
      if (users.length && !reassign) return HttpResponse.json({ code: "channel_in_use", message: "Accounts post here.", accounts: users.map((a) => ({ id: a.id, handle: a.handle })) } satisfies Schemas["Error"], { status: 409 });
      const target = reassign === "default" ? null : Number(reassign);
      // Moved to the new place, unless the account already sends there (that destination stays).
      users.forEach((a) => {
        const already = a.destinations.some((d) => d.channel_id === target);
        a.destinations = a.destinations.flatMap((d) => (d.channel_id !== channel.id ? [d] : already ? [] : [{ ...d, channel_id: target }]));
      });
      s.channels = s.channels.filter((c) => c !== channel);
      return new HttpResponse(null, { status: 204 });
    }),
    http.get("*/api/accounts/", () => offline() ?? HttpResponse.json(page([...s.accounts].reverse().map((a) => toAccount(s, a))))),
    http.post("*/api/accounts/check_all/", async ({ request }) => {
      const refused = guard(request, {});
      if (refused) return refused;
      const running = s.jobs.find((j) => !j.final);
      if (running) return HttpResponse.json({ ...running, created: false } satisfies CheckJob, { status: 202 });
      const active = s.accounts.filter((a) => a.is_active);
      const job: CheckJob = {
        id: `job-${s.jobs.length + 1}`,
        kind: "check_all",
        url: `/api/check-jobs/job-${s.jobs.length + 1}/`,
        status: "queued",
        final: false,
        status_label: "Waiting to start",
        message: "The check starts in a moment.",
        result: null,
        problem: null,
        requested_at: now(),
        started_at: null,
        finished_at: null,
        updated_at: null,
        progress: { total: active.length, done: 0, percent: 0, current: null },
        counts: { checked: 0, failed: 0, skipped: s.accounts.length - active.length, already_checking: 0, waiting: active.length, new_posts: 0, posts_sent: 0 },
        accounts: [
          ...active.map((a) => ({ id: a.id, handle: a.handle, result: "waiting" as const, reason: null, message: "Waiting to be checked.", problem: null, first_check: false, new_posts: 0, posts_sent: 0, not_announced: 0, state: "active" as const, state_label: "Active" })),
          ...s.accounts.filter((a) => !a.is_active).map((a) => ({ id: a.id, handle: a.handle, result: "skipped" as const, reason: "paused" as const, message: "Paused, so it wasn't checked.", problem: null, first_check: false, new_posts: 0, posts_sent: 0, not_announced: 0, state: "paused" as const, state_label: "Paused" })),
        ],
        checker_running: true,
        poll_after_seconds: 0.05,
      };
      s.jobs.unshift(job);
      return HttpResponse.json({ ...job, created: true } satisfies CheckJob, { status: 202 });
    }),
    http.get("*/api/check-jobs/", () => {
      if (s.offline) return HttpResponse.error();
      const job = s.jobs[0];
      if (job) advance(s, job);
      return HttpResponse.json({ results: s.jobs.slice(0, 1) } satisfies Schemas["CheckJobList"]);
    }),
    http.post("*/api/accounts/", async ({ request }) => {
      const body = await read(request);
      const refused = guard(request, body);
      if (refused) return refused;
      const handle = String(body.handle ?? "").replace(/^@/, "").trim().toLowerCase();
      if (!handle) return HttpResponse.json({ handle: ["This field may not be blank."] } satisfies FieldErrors, { status: 400 });
      if (s.accounts.some((a) => a.handle === handle)) return HttpResponse.json({ handle: [`You're already watching @${handle}.`] } satisfies FieldErrors, { status: 400 });
      const destinations = checkDestinations(s, body.destinations ?? [{ channel_id: null, role_id: "" }]);
      if (destinations.errors) return HttpResponse.json(destinations.errors, { status: 400 });
      const account: FakeAccount = {
        id: Math.max(0, ...s.accounts.map((a) => a.id)) + 1,
        handle,
        display_name: body.display_name ?? "",
        destinations: destinations.value!,
        is_active: body.is_active ?? true,
        first_poll_done: false,
        announce_posts: body.announce_posts ?? true,
        announce_reposts: body.announce_reposts ?? true,
        announce_replies: body.announce_replies ?? false,
        last_checked_at: null,
        created_at: now(),
      };
      s.accounts.push(account);
      record("account_added", "info", `Started watching @${handle}.`, { account });
      return HttpResponse.json(toAccount(s, account), { status: 201 });
    }),
    http.patch("*/api/accounts/:id/", async ({ request, params }) => {
      const body = await read(request);
      const refused = guard(request, body);
      if (refused) return refused;
      const account = s.accounts.find((a) => a.id === Number(params.id))!;
      const { destinations, ...rest } = body;
      if (destinations !== undefined) {
        const checked = checkDestinations(s, destinations);
        if (checked.errors) return HttpResponse.json(checked.errors, { status: 400 });
        account.destinations = checked.value!;
      }
      Object.assign(account, rest);
      return HttpResponse.json(toAccount(s, account));
    }),
    http.delete("*/api/accounts/:id/", ({ request, params }) => {
      const refused = guard(request, null);
      if (refused) return refused;
      s.accounts = s.accounts.filter((a) => a.id !== Number(params.id));
      return new HttpResponse(null, { status: 204 });
    }),
    http.post("*/api/accounts/:id/poll_now/", async ({ request, params }) => {
      const refused = guard(request, {});
      if (refused) return refused;
      const account = s.accounts.find((a) => a.id === Number(params.id))!;
      const first = !account.first_poll_done;
      if (!s.handle || !s.password) {
        return HttpResponse.json({ new_posts_announced: 0, was_first_poll: first, error: "The bot's Bluesky login isn't set up yet.", problem: { category: "bluesky_not_configured", title: "The Bluesky login isn't set up", explanation: "Add it in Settings.", fix: "update_login" }, state: "waiting_first_check", state_label: "", state_message: "", first_check: toAccount(s, account).first_check } satisfies PollResult);
      }
      account.first_poll_done = true;
      account.last_checked_at = now();
      const after = toAccount(s, account);
      return HttpResponse.json({
        new_posts_announced: 0,
        was_first_poll: first,
        state: after.state,
        state_label: after.state_label,
        state_message: "",
        first_check: after.first_check,
        problem: null,
        check: { result: "checked", message: first ? "First check done. Noted 2 existing posts; none were sent." : "No new posts.", new_posts: 0, posts_sent: 0, not_announced: 0 },
      } satisfies PollResult);
    }),
    http.get("*/api/activity/", ({ request }) => {
      if (s.offline) return HttpResponse.error();
      const url = new URL(request.url);
      const group = url.searchParams.get("group") ?? "all";
      const size = Number(url.searchParams.get("page_size") ?? 50);
      const after = Number(url.searchParams.get("cursor") ?? 0);
      let list = s.events;
      if (group === "sent") list = list.filter((e) => e.type === "post_sent");
      if (group === "problems") list = list.filter((e) => e.level === "warning" || e.level === "error");
      const slice = list.slice(after, after + size);
      const next = after + size < list.length ? `http://localhost:3000/api/activity/?group=${group}&page_size=${size}&cursor=${after + size}` : null;
      return HttpResponse.json({ next, previous: null, results: slice } satisfies CursorPage<ActivityEvent>);
    }),
    http.get("*/api/bluesky/profile/", ({ request }) => {
      const handle = (new URL(request.url).searchParams.get("handle") ?? "").replace(/^@/, "").trim().toLowerCase();
      const base = { query: handle, handle, already_watched: false, watched_account_id: null, problem: null, suggestion: null, retryable: false, retry_after_seconds: null, cached: false };
      if (/\s/.test(handle)) return HttpResponse.json({ ...base, result: "invalid", exists: null, profile: null, code: "invalid_handle", message: "A handle can't contain spaces.", suggestion: handle.replace(/\s/g, "") } satisfies ProfileLookup, { status: 400 });
      const name = PROFILES[handle];
      if (!name) return HttpResponse.json({ ...base, result: "not_found", exists: false, profile: null, code: "handle_not_found", message: `No Bluesky account is called @${handle}. Check the spelling.` } satisfies ProfileLookup);
      const watched = s.accounts.find((a) => a.handle === handle);
      return HttpResponse.json({
        ...base,
        result: "found",
        exists: true,
        code: "found",
        already_watched: Boolean(watched),
        watched_account_id: watched?.id ?? null,
        message: watched ? `You're already watching @${handle}.` : `Found @${handle}.`,
        profile: { did: "did:plc:" + handle, handle, display_name: name, avatar_url: null, description: "", followers_count: 10, posts_count: 10, watch_as: handle },
      } satisfies ProfileLookup);
    }),
  ];
}
