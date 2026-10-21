import { useNavigate } from "react-router-dom";
import type { BotStatus } from "../api/types";
import { Banner, Button } from "../components/ui";
import { fixLink } from "../lib/fixes";
import { fullTime } from "../lib/time";

/** The most important current problem, with its fix one click away. Clears itself once fixed. */
export function ProblemBanner({ status, onMore }: { status: BotStatus | undefined; onMore: () => void }) {
  const navigate = useNavigate();
  if (!status) return null;
  if (status.state === "unavailable") {
    const s = status.scheduler;
    return (
      <Banner
        tone="bad"
        title={s.message}
        actions={
          <Button size="sm" onClick={onMore}>
            What to do
          </Button>
        }
      >
        {s.problem?.explanation ?? "Nothing is being checked right now."}
      </Banner>
    );
  }
  const problem = status.problems[0];
  if (!problem) return null;
  const fix = fixLink(problem.fix);
  const others = status.problems.length - 1;
  // The message often just restates the title ("Bluesky didn't accept the login."); then the explanation is enough.
  const plain = (text: string) => text.toLowerCase().replace(/[.\s]+$/, "");
  const message = plain(problem.message).startsWith(plain(problem.title)) ? "" : problem.message;
  return (
    <Banner
      tone={problem.severity === "warning" ? "warn" : "bad"}
      title={problem.title}
      actions={
        <>
          {fix ? (
            <Button size="sm" onClick={() => navigate(fix.to)}>
              {fix.label}
            </Button>
          ) : null}
          {others > 0 ? (
            <Button size="sm" variant="ghost" onClick={onMore}>
              {others} more
            </Button>
          ) : null}
        </>
      }
    >
      {message ? <span title={problem.since ? `Since ${fullTime(problem.since)}` : undefined}>{message} </span> : null}
      {problem.explanation}
    </Banner>
  );
}
