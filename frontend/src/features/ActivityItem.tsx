import { Link } from "react-router-dom";
import {
  AtSign,
  Check,
  CircleAlert,
  CircleCheck,
  Clock,
  Hash,
  Lock,
  Pause,
  Play,
  Send,
  UserMinus,
  UserPlus,
  type LucideIcon,
} from "lucide-react";
import type { ActivityEvent } from "../api/types";
import { ICON } from "../components/ui";
import { fixLink } from "../lib/fixes";
import { safeBlueskyLink } from "../lib/people";
import { ago, fullTime } from "../lib/time";

type Look = { tone: "ok" | "bad" | "warn" | "idle" | "info"; icon: LucideIcon; title: (e: ActivityEvent) => string };

const chan = (e: ActivityEvent) => (e.channel?.label ? `#${e.channel.label}` : "Discord");

const LOOKS: Record<string, Look> = {
  post_sent: { tone: "ok", icon: Send, title: (e) => `Sent to ${chan(e)}${e.is_retry ? " (sent again)" : ""}` },
  send_failed: { tone: "warn", icon: CircleAlert, title: (e) => `Couldn't send to ${chan(e)} yet` },
  send_gave_up: { tone: "bad", icon: CircleAlert, title: (e) => `Not sent to ${chan(e)}` },
  post_not_sent: { tone: "warn", icon: Hash, title: () => "Not sent: no channel to post to" },
  mention_not_sent: { tone: "warn", icon: AtSign, title: (e) => `Sent to ${chan(e)} without the role mention` },
  check_failed: { tone: "warn", icon: Clock, title: () => "Check didn't work" },
  check_recovered: { tone: "ok", icon: CircleCheck, title: () => "Checks work again" },
  first_check_done: { tone: "info", icon: Check, title: () => "First check done" },
  login_failed: { tone: "bad", icon: Lock, title: () => "Bluesky login rejected" },
  login_recovered: { tone: "ok", icon: Lock, title: () => "Bluesky login works again" },
  cycle_failed: { tone: "bad", icon: CircleAlert, title: () => "A check stopped early" },
  checker_started: { tone: "idle", icon: Play, title: () => "Checker started" },
  checker_stopped: { tone: "idle", icon: Pause, title: () => "Checker stopped" },
  account_added: { tone: "info", icon: UserPlus, title: () => "Started watching" },
  account_removed: { tone: "idle", icon: UserMinus, title: () => "Stopped watching" },
  channel_test_sent: { tone: "ok", icon: Send, title: (e) => `Test message sent to ${chan(e)}` },
  channel_test_failed: { tone: "bad", icon: CircleAlert, title: (e) => `Test message to ${chan(e)} didn't arrive` },
};

const BY_LEVEL: Record<string, Look> = {
  success: { tone: "ok", icon: CircleCheck, title: () => "Done" },
  info: { tone: "info", icon: Check, title: () => "Note" },
  warning: { tone: "warn", icon: CircleAlert, title: () => "Warning" },
  error: { tone: "bad", icon: CircleAlert, title: () => "Problem" },
};

export function ActivityItem({ event, now, wide }: { event: ActivityEvent; now: number; wide?: boolean }) {
  const look = LOOKS[event.type] ?? BY_LEVEL[event.level] ?? BY_LEVEL.info;
  const Icon = look.icon;
  const who = event.account?.label || (event.channel?.label ? `#${event.channel.label}` : "Bot");
  const text = typeof event.detail?.post_text === "string" ? event.detail.post_text : "";
  const postLink = safeBlueskyLink(event.detail?.post_url);
  const fix = event.level === "error" || event.level === "warning" ? fixLink(event.problem?.fix) : null;
  const when = (
    <div className="t num" title={fullTime(event.created_at)}>
      <time dateTime={event.created_at}>{ago(event.created_at, now)}</time>
      {event.repeat_count > 1 ? (
        <span className="rep" title={`Happened ${event.repeat_count} times, last ${ago(event.last_seen_at, now)}`}>
          ×{event.repeat_count}
        </span>
      ) : null}
    </div>
  );
  return (
    <li>
      <div className={`ic ${look.tone}`}>
        <Icon {...ICON} />
      </div>
      <div style={{ minWidth: 0 }}>
        <div className="m">
          <b>{who}</b> · {look.title(event)}
        </div>
        <div className="q">{event.type === "post_sent" && text ? `“${text}”` : event.message}</div>
        {wide ? null : when}
        {postLink ? (
          <a className="fix" href={postLink} target="_blank" rel="noopener noreferrer">
            Open the post on Bluesky
          </a>
        ) : null}
        {fix ? (
          <Link to={fix.to} className="fix" style={{ marginLeft: postLink ? 12 : 0 }}>
            {fix.label} →
          </Link>
        ) : null}
      </div>
      {wide ? when : null}
    </li>
  );
}
