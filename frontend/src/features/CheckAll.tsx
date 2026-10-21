import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, RefreshCw, TriangleAlert, X } from "lucide-react";
import { errorMessage } from "../api/client";
import { useCheckAll, useLatestJob } from "../api/queries";
import type { CheckJob, JobAccountResult } from "../api/types";
import { Badge, Button, ICON, IconButton, useToast, type Tone } from "../components/ui";
import { fixLink } from "../lib/fixes";
import { recall, remember } from "../lib/storage";
import { ago, plural } from "../lib/time";

const SHOW_FINISHED_FOR_MS = 15 * 60_000;

const ROW: Record<JobAccountResult, [Tone, string]> = {
  waiting: ["idle", "Waiting"],
  checking: ["busy", "Checking…"],
  checked: ["ok", "Checked"],
  failed: ["bad", "Didn't work"],
  skipped: ["idle", "Skipped"],
  already_checking: ["idle", "Already being checked"],
};

function finishedTone(job: CheckJob): "ok" | "warn" | "bad" {
  if (job.status === "finished" && (job.result === "ok" || job.result === "nothing_to_check")) return "ok";
  if (job.status === "finished" && job.result === "partial") return "warn";
  return "bad";
}

function summary(job: CheckJob): string {
  const c = job.counts;
  const parts = [`${c.checked} checked`];
  if (c.failed) parts.push(`${c.failed} didn't work`);
  if (c.skipped) parts.push(`${c.skipped} skipped`);
  parts.push(c.posts_sent ? `${plural(c.posts_sent, "new post")} sent` : "no new posts sent");
  return parts.join(" · ");
}

/** "Check all now": the button, and the job's progress and results. */
export function useCheckAllJob() {
  const latest = useLatestJob();
  const start = useCheckAll();
  const client = useQueryClient();
  const toast = useToast();
  const [dismissed, setDismissed] = useState(() => recall("dismissed-check"));
  const job = latest.data ?? null;
  const running = Boolean(job && !job.final);
  const seen = useRef<string | null>(null);

  // When a job this page watched finishes, refresh everything it changed and say how it went.
  useEffect(() => {
    if (!job) return;
    if (!job.final) {
      seen.current = job.id;
      return;
    }
    if (seen.current === job.id) {
      seen.current = null;
      client.invalidateQueries({ predicate: (q) => !["lookup", "health", "check-jobs"].includes(String(q.queryKey[0])) });
      toast(job.message || "Check finished.", finishedTone(job) === "bad" ? "bad" : "ok");
    }
  }, [job, client, toast]);

  const visible =
    job !== null &&
    job.id !== dismissed &&
    (!job.final || Date.now() - new Date(job.finished_at ?? job.requested_at).getTime() < SHOW_FINISHED_FOR_MS);

  return {
    job,
    running,
    visible,
    starting: start.isPending,
    start: () =>
      start.mutate(undefined, {
        onSuccess: (created) => {
          remember("dismissed-check", null);
          setDismissed(null);
          seen.current = created.id;
          if (created.created === false) toast("A check of every account is already running. Showing its progress.");
        },
        onError: (error) => toast(errorMessage(error), "bad"),
      }),
    dismiss: () => {
      if (!job) return;
      remember("dismissed-check", job.id);
      setDismissed(job.id);
    },
  };
}

export function CheckAllButton({ check }: { check: ReturnType<typeof useCheckAllJob> }) {
  return (
    <Button icon={RefreshCw} onClick={check.start} busy={check.starting || check.running} busyLabel="Checking all…">
      Check all now
    </Button>
  );
}

export function CheckAllPanel({ check, now }: { check: ReturnType<typeof useCheckAllJob>; now: number }) {
  const { job, visible } = check;
  if (!job || !visible) return null;
  const tone = job.final ? finishedTone(job) : "busy";
  const Icon = tone === "ok" ? CircleCheck : tone === "warn" ? TriangleAlert : CircleAlert;
  const fix = job.problem ? fixLink(job.problem.fix) : null;
  const iconStyle =
    tone === "busy"
      ? { background: "var(--accent-soft)", color: "var(--link)" }
      : { background: `var(--${tone}-bg)`, color: `var(--${tone})` };

  return (
    <section className="panel" aria-labelledby="job-title" aria-busy={!job.final}>
      <div className="job">
        <div className="job-top">
          <div className="icon-wrap" style={iconStyle}>
            {job.final ? <Icon {...ICON} /> : <span className="spin" aria-hidden="true" />}
          </div>
          <div style={{ minWidth: 0, flex: 1 }}>
            <b id="job-title">{job.final ? (tone === "ok" ? "Check all finished" : job.status_label) : `Check all: ${job.status_label.toLowerCase()}`}</b>
            <p aria-live="polite">{job.message}</p>
          </div>
          {job.final ? (
            <div className="actions">
              <span className="muted" style={{ fontSize: 13 }}>
                {ago(job.finished_at, now)}
              </span>
              <IconButton icon={X} label="Hide this result" onClick={check.dismiss} />
            </div>
          ) : null}
        </div>
        {job.final ? null : (
          <div>
            <div className="progress" role="progressbar" aria-label="Check all progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={job.progress.percent}>
              <i style={{ width: `${Math.max(job.progress.percent, job.status === "queued" ? 2 : 6)}%` }} />
            </div>
            <p className="muted num" style={{ fontSize: 13, marginTop: 6 }}>
              {job.progress.total ? `${job.progress.done} of ${plural(job.progress.total, "account")} done` : "Starting…"}
              {job.progress.current ? ` · now checking @${job.progress.current.handle}` : ""}
            </p>
          </div>
        )}
        {job.final ? (
          <p className="num" style={{ fontSize: 13 }}>
            {summary(job)}
            {job.problem ? (
              <>
                {" "}
                · {job.problem.explanation}{" "}
                {fix ? (
                  <Link to={fix.to} style={{ fontWeight: 600 }}>
                    {fix.label} →
                  </Link>
                ) : null}
              </>
            ) : null}
          </p>
        ) : null}
        {job.accounts.length ? (
          <details className="job-more" open={!job.final && job.accounts.length <= 8}>
            <summary>Each account ({job.accounts.length})</summary>
            <ul className="job-list">
              {job.accounts.map((row) => {
                const [rowTone, label] = ROW[row.result] ?? ["idle", row.result];
                return (
                  <li key={row.id}>
                    <span className="h">@{row.handle}</span>
                    <Badge tone={rowTone}>{label}</Badge>
                    <span className="msg">{row.message}</span>
                  </li>
                );
              })}
            </ul>
          </details>
        ) : null}
      </div>
    </section>
  );
}
