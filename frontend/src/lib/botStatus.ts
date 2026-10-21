import { OfflineError } from "../api/client";
import type { BotStatus } from "../api/types";
import type { SetupProgress } from "./setup";
import { untilText } from "./time";

export interface StatusView {
  tone: "ok" | "warn" | "bad" | "idle";
  label: string;
  sub: string;
  /** The panel can't reach the bot at all (stopped, or Docker Desktop closed). */
  offline: boolean;
}

const CHECKER_LABELS: Record<string, string> = {
  not_started: "Checker not started",
  stopped: "Checker stopped",
  stalled: "Checker stuck",
};

/** One line for "how is the bot doing", shared by the sidebar, the summary and the status dialog. */
export function statusView(status: BotStatus | undefined, error: unknown, setup: SetupProgress, now = Date.now()): StatusView {
  if (error instanceof OfflineError) {
    return { tone: "idle", label: "Not running", sub: "Can't reach the bot", offline: true };
  }
  if (!status) {
    if (error) return { tone: "bad", label: "Status unavailable", sub: "The bot didn't answer properly", offline: false };
    return { tone: "idle", label: "Connecting…", sub: "Asking the bot how it's doing", offline: false };
  }
  switch (status.state) {
    case "unavailable":
      return { tone: "bad", label: CHECKER_LABELS[status.scheduler.state] ?? "Not checking", sub: status.scheduler.message, offline: false };
    case "not_configured":
      return { tone: "idle", label: "Not set up yet", sub: `Setup: ${setup.done} of 4 done`, offline: false };
    case "problem":
      return { tone: "warn", label: "Needs attention", sub: status.problems[0]?.title ?? status.message, offline: false };
    default: {
      const s = status.scheduler;
      let sub = status.message;
      if (s.checking_now) sub = "Checking now…";
      else if (status.counts.accounts_active === 0) sub = "All accounts are paused";
      else if (s.next_check_at) sub = `Next check ${untilText(s.next_check_at, now)}`;
      return { tone: "ok", label: "Running", sub, offline: false };
    }
  }
}
