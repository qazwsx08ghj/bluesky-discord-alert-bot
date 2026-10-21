// Shapes of the Django REST API, as documented in docs/API.md.
// Times are ISO 8601 strings. Secrets never appear: webhook URLs come back masked and
// the App Password only as `has_bluesky_password`.

export type Fix =
  | "update_login"
  | "update_webhook"
  | "choose_channel"
  | "check_handle"
  | "restart_bot"
  | "wait"
  | "add_account"
  | "none";

export interface Problem {
  category: string;
  title: string;
  explanation: string;
  fix: Fix | string;
  severity?: "error" | "warning" | "setup";
}

export interface CurrentProblem extends Problem {
  code: string;
  message: string;
  since: string | null;
  account?: { id: number; handle: string };
  channel?: { id: number; name: string };
}

export type BotState = "running" | "problem" | "not_configured" | "unavailable";
export type LoginState = "ok" | "failed" | "rate_limited" | "not_configured" | "unknown";

export interface BlueskyLogin {
  state: LoginState;
  handle: string;
  problem: Problem | null;
  retry_after: string | null;
}

export interface LastCheck {
  started_at?: string | null;
  finished_at: string | null;
  result: string | null;
  message: string;
  problem: Problem | null;
  accounts_checked: number | null;
  accounts_failed: number | null;
  posts_sent: number | null;
}

export interface SchedulerHealth {
  state: "running" | "checking" | "stalled" | "stopped" | "not_started";
  healthy: boolean;
  message: string;
  problem: Problem | null;
  heartbeat_at: string | null;
  heartbeat_age_seconds: number | null;
  active_since: string | null;
  check_interval_minutes: number;
  checking_now: boolean;
  current_check_started_at: string | null;
  last_check: LastCheck | null;
  next_check_at: string | null;
}

export interface BotStatus {
  state: BotState;
  headline: string;
  message: string;
  checked_at: string;
  scheduler: SchedulerHealth;
  bluesky_login: BlueskyLogin;
  setup: {
    bluesky_login: boolean;
    discord_channel: boolean;
    accounts: boolean;
    complete: boolean;
    missing: CurrentProblem[];
  };
  counts: {
    accounts_total: number;
    accounts_active: number;
    channels: number;
    pending_deliveries: number;
    deliveries_waiting: number;
    deliveries_retrying: number;
    sent_last_24h: number;
    failed_sends_last_24h: number;
  };
  problems: CurrentProblem[];
  recent_errors: ActivityEvent[];
}

export interface Health {
  status: "ok" | "updating" | "starting" | "error";
  version: string;
  database: boolean;
  migrations_pending: number;
  checker: string;
}

export interface Paged<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export type AccountState = "checking" | "paused" | "error" | "waiting_first_check" | "active";

export interface Account {
  id: number;
  handle: string;
  display_name: string;
  is_active: boolean;
  channel_id: number | null;
  channel: { id: number; name: string; is_default: boolean } | null;
  uses_default_channel: boolean;
  announce_posts: boolean;
  announce_reposts: boolean;
  announce_replies: boolean;
  first_poll_done: boolean;
  created_at: string;
  state: AccountState;
  state_label: string;
  state_message: string;
  checking_now: boolean;
  problem: (Problem & { severity: "error" | "warning" }) | null;
  first_check: { done: boolean; at: string | null; seeded_posts: number | null };
  last_checked_at: string | null;
  last_check_ok: boolean | null;
  last_success_at: string | null;
  last_error: (Problem & { at: string | null; ongoing: boolean }) | null;
}

export interface AccountInput {
  handle?: string;
  display_name?: string;
  channel_id?: number | null;
  is_active?: boolean;
  announce_posts?: boolean;
  announce_reposts?: boolean;
  announce_replies?: boolean;
}

export interface PollResult {
  new_posts_announced: number;
  was_first_poll: boolean;
  state: AccountState;
  state_label: string;
  state_message: string;
  first_check: Account["first_check"];
  problem: Problem | null;
  check?: { result: string; message: string; new_posts: number; posts_sent: number; not_announced: number };
  error?: string;
}

export interface Channel {
  id: number;
  name: string;
  url_masked: string;
  is_default: boolean;
  created_at: string;
  updated_at: string;
  account_count: number;
  last_delivery: { at: string; ok: boolean; error: string } | null;
}

export interface ChannelInput {
  name?: string;
  url?: string;
  is_default?: boolean;
}

export interface Settings {
  bluesky_handle: string;
  has_bluesky_password: boolean;
  default_channel: { id: number; name: string } | null;
  poll_interval_minutes: number;
  settings_version: number;
  updated_at: string | null;
  bluesky_login: BlueskyLogin;
}

export interface SettingsInput {
  bluesky_handle?: string;
  bluesky_password?: string;
  poll_interval_minutes?: number;
}

export type LoginTestResult = "ok" | "rejected" | "app_password_needed" | "rate_limited" | "unavailable" | "error" | "invalid";

export interface LoginTest {
  ok: boolean;
  result: LoginTestResult;
  code: string;
  message: string;
  problem: Problem | null;
  handle?: string;
  account?: { did: string; handle: string } | null;
  matches_saved?: boolean;
  saved: false;
  password_looks_like_app_password?: boolean;
  warning?: string | null;
  retry_after_seconds?: number | null;
  fields?: Record<string, string[]>;
}

export type LookupResult = "found" | "not_found" | "unavailable" | "invalid" | "rate_limited" | "error";

export interface ProfileLookup {
  query: string;
  handle: string;
  result: LookupResult;
  exists: boolean | null;
  profile: {
    did: string;
    handle: string;
    display_name: string;
    avatar_url: string | null;
    description: string;
    followers_count: number | null;
    posts_count: number | null;
    watch_as: string;
  } | null;
  already_watched: boolean;
  watched_account_id: number | null;
  code: string;
  message: string;
  problem: Problem | null;
  suggestion: string | null;
  retryable: boolean;
  retry_after_seconds: number | null;
}

export type ActivityLevel = "success" | "info" | "warning" | "error";

export interface ActivityEvent {
  id: number;
  created_at: string;
  last_seen_at: string | null;
  repeat_count: number;
  type: string;
  level: ActivityLevel;
  message: string;
  problem: Problem | null;
  account: { id: number; label: string } | null;
  channel: { id: number; label: string } | null;
  attempt: number | null;
  is_retry: boolean;
  detail: { post_url?: string; post_text?: string; seeded_posts?: number; [key: string]: unknown };
}

export interface CursorPage<T> {
  next: string | null;
  previous: string | null;
  results: T[];
}

export type JobStatus = "queued" | "running" | "finished" | "interrupted" | "expired";
export type JobAccountResult = "waiting" | "checking" | "checked" | "failed" | "skipped" | "already_checking";

export interface CheckJob {
  id: string;
  kind: "check_all";
  url: string;
  status: JobStatus;
  final: boolean;
  status_label: string;
  message: string;
  result: "ok" | "partial" | "failed" | "nothing_to_check" | "not_configured" | "login_failed" | null;
  problem: Problem | null;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string | null;
  progress: { total: number; done: number; percent: number; current: { id: number; handle: string } | null };
  counts: {
    checked: number;
    failed: number;
    skipped: number;
    already_checking: number;
    waiting: number;
    new_posts: number;
    posts_sent: number;
  };
  accounts: {
    id: number;
    handle: string;
    result: JobAccountResult;
    reason: string | null;
    message: string;
    problem: Problem | null;
    first_check: boolean;
    new_posts: number;
    posts_sent: number;
    not_announced: number;
    state: AccountState;
    state_label: string;
  }[];
  checker_running: boolean | null;
  poll_after_seconds: number;
  created?: boolean;
}
