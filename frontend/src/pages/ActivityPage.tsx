import { useState } from "react";
import { Activity, RefreshCw } from "lucide-react";
import { errorMessage } from "../api/client";
import { useActivity, type ActivityGroup } from "../api/queries";
import { useShell, useTitle } from "../components/Shell";
import { Button, Empty, SkeletonLines } from "../components/ui";
import { ActivityItem } from "../features/ActivityItem";
import { ProblemBanner } from "../features/ProblemBanner";

const FILTERS: { value: ActivityGroup; label: string; empty: string }[] = [
  { value: "all", label: "All", empty: "When the bot sends a post or runs into a problem, it shows up here." },
  { value: "sent", label: "Sent", empty: "No posts were sent to Discord in the last 30 days." },
  { value: "problems", label: "Problems", empty: "No problems in the last 30 days." },
];

export function ActivityPage() {
  useTitle("Activity");
  const { status, now, openStatus } = useShell();
  const [group, setGroup] = useState<ActivityGroup>("all");
  const activity = useActivity(group);
  const events = activity.data?.pages.flatMap((p) => p.results) ?? [];
  const filter = FILTERS.find((f) => f.value === group)!;

  return (
    <>
      <div className="head">
        <div>
          <h1>Activity</h1>
          <p>Everything the bot sent to Discord and anything that went wrong, newest first.</p>
        </div>
        <div className="seg" role="group" aria-label="Show">
          {FILTERS.map((f) => (
            <button key={f.value} type="button" aria-pressed={group === f.value} onClick={() => setGroup(f.value)}>
              {f.label}
            </button>
          ))}
        </div>
      </div>
      <ProblemBanner status={status} onMore={openStatus} />
      <section className="panel" aria-label={`Activity: ${filter.label}`} aria-busy={activity.isFetching}>
        {activity.isPending ? (
          <SkeletonLines lines={5} label="Loading the activity…" />
        ) : activity.isError && !activity.data ? (
          <Empty
            icon={Activity}
            title="Couldn't load the activity"
            tone="bad"
            action={
              <Button icon={RefreshCw} onClick={() => activity.refetch()} busy={activity.isFetching}>
                Try again
              </Button>
            }
          >
            {errorMessage(activity.error)}
          </Empty>
        ) : events.length ? (
          <ul className="feed wide">
            {events.map((e) => (
              <ActivityItem key={e.id} event={e} now={now} wide />
            ))}
          </ul>
        ) : (
          <Empty icon={Activity} title={group === "all" ? "No activity yet" : "Nothing here"}>
            {filter.empty}
          </Empty>
        )}
        {events.length ? (
          <div className="panel-f">
            <span>The log keeps the last 30 days. Repeated problems are counted on one line.</span>
            {activity.hasNextPage ? (
              <Button size="sm" onClick={() => activity.fetchNextPage()} busy={activity.isFetchingNextPage} busyLabel="Loading…">
                Show older
              </Button>
            ) : null}
          </div>
        ) : null}
      </section>
    </>
  );
}
