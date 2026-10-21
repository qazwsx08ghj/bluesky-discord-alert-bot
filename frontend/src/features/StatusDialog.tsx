import { useId } from "react";
import { Link } from "react-router-dom";
import { X } from "lucide-react";
import type { BotStatus, LoginState } from "../api/types";
import { Dialog, Dot, IconButton } from "../components/ui";
import type { StatusView } from "../lib/botStatus";
import { fixLink } from "../lib/fixes";
import { ago, fullTime, plural, untilText } from "../lib/time";

const LOGIN: Record<LoginState, { tone: "ok" | "warn" | "bad" | "idle"; text: string }> = {
  ok: { tone: "ok", text: "Working" },
  unknown: { tone: "idle", text: "Saved; not tried yet. The next check uses it." },
  failed: { tone: "bad", text: "Rejected by Bluesky" },
  rate_limited: { tone: "warn", text: "Bluesky asked the bot to wait before signing in again" },
  not_configured: { tone: "idle", text: "Not set up yet" },
};

/** "Is it working?" in one place: the checker, the login, the last check and current problems. */
export function StatusDialog({
  open,
  onClose,
  status,
  view,
  version,
  now,
}: {
  open: boolean;
  onClose: () => void;
  status: BotStatus | undefined;
  view: StatusView;
  version?: string;
  now: number;
}) {
  const id = useId();
  const s = status?.scheduler;
  const last = s?.last_check;
  const login = status ? LOGIN[status.bluesky_login.state] : null;
  return (
    <Dialog open={open} onClose={onClose} labelledBy={`${id}-t`} className="modal wide">
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
        <div style={{ flex: 1 }}>
          <h2 id={`${id}-t`} style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <Dot tone={view.tone} />
            {view.offline ? "The bot isn't running" : (status?.headline ?? view.label)}
          </h2>
          <p className="lead" style={{ margin: "6px 0 0" }}>
            {view.offline
              ? "This page can't reach the bot, so nothing is being checked or sent."
              : (status?.message ?? view.sub)}
          </p>
        </div>
        <IconButton icon={X} label="Close" onClick={onClose} />
      </div>

      {view.offline ? (
        <ol style={{ margin: "16px 0 0", paddingLeft: 20, color: "var(--text-2)" }}>
          <li>Open Docker Desktop and wait until it says "Engine running".</li>
          <li>
            Double-click <b>start.bat</b> in the bot's folder.
          </li>
          <li>This page reconnects by itself once the bot answers.</li>
        </ol>
      ) : status && s ? (
        <>
          <div className="status-rows">
            <div>
              <Dot tone={s.healthy ? "ok" : "bad"} />
              <div>
                <b>Checker</b>
                <p>
                  {s.message}
                  {s.healthy && s.next_check_at && !s.checking_now ? ` Next check ${untilText(s.next_check_at, now)}.` : ""}
                  {s.checking_now ? " Checking now." : ""} Checks every {plural(s.check_interval_minutes, "minute")}.
                </p>
                {!s.healthy && s.problem ? <p>{s.problem.explanation}</p> : null}
              </div>
            </div>
            {login ? (
              <div>
                <Dot tone={login.tone} />
                <div>
                  <b>Bluesky login</b>
                  <p>
                    {status.bluesky_login.handle ? `@${status.bluesky_login.handle}: ` : ""}
                    {login.text}
                  </p>
                </div>
              </div>
            ) : null}
            <div>
              <Dot tone={last?.result && !["ok", "interrupted"].includes(last.result) ? "warn" : last?.finished_at ? "ok" : "idle"} />
              <div>
                <b>Last scheduled check</b>
                <p title={fullTime(last?.finished_at)}>
                  {last?.finished_at
                    ? `${ago(last.finished_at, now)}: ${last.message || "Done."}${last.posts_sent ? ` ${plural(last.posts_sent, "post")} sent.` : ""}`
                    : `None has finished since the checker started${s.next_check_at ? `; the first is due ${untilText(s.next_check_at, now)}` : ""}.`}
                </p>
              </div>
            </div>
            <div>
              <Dot tone="idle" />
              <div>
                <b>Last 24 hours</b>
                <p>
                  {plural(status.counts.sent_last_24h, "post")} sent
                  {status.counts.failed_sends_last_24h ? `, ${plural(status.counts.failed_sends_last_24h, "post")} couldn't be sent` : ""}
                  {status.counts.deliveries_retrying ? `, ${plural(status.counts.deliveries_retrying, "post")} waiting to be sent again` : ""}.
                </p>
              </div>
            </div>
          </div>

          {status.problems.length ? (
            <>
              <h3 style={{ fontSize: 14, fontWeight: 600, marginTop: 18 }}>What needs attention</h3>
              <ul className="problem-list">
                {status.problems.map((p) => {
                  const fix = fixLink(p.fix);
                  return (
                    <li key={p.code + (p.account?.id ?? "") + (p.channel?.id ?? "")}>
                      <b>{p.title}</b>
                      <p>{p.message}</p>
                      <p>{p.explanation}</p>
                      {fix ? (
                        <Link to={fix.to} onClick={onClose} className="fix" style={{ fontWeight: 600, fontSize: 13 }}>
                          {fix.label} →
                        </Link>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            </>
          ) : null}
        </>
      ) : null}

      <p className="muted" style={{ fontSize: 12, marginTop: 18 }}>
        {version ? `Version ${version}. ` : ""}
        {status ? `Updated ${ago(status.checked_at, now)}.` : ""}
      </p>
    </Dialog>
  );
}
