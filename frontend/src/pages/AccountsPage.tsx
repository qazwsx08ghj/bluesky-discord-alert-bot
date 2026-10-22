import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Activity, ExternalLink, Hash, MoreHorizontal, Pause, Pencil, Play, Plus, RefreshCw, Trash2, Users } from "lucide-react";
import { ApiError, errorMessage } from "../api/client";
import { useAccounts, useChannels, useCheckNow, useRecentActivity, useRemoveAccount, useUpdateAccount } from "../api/queries";
import type { Account, Channel } from "../api/types";
import { useShell, useTitle } from "../components/Shell";
import { Avatar, Badge, Banner, Button, Confirm, Dot, Empty, ICON, Menu, SkeletonLines, SkeletonRows, useToast } from "../components/ui";
import { useAccountDrawer } from "../features/AccountDrawer";
import { ActivityItem } from "../features/ActivityItem";
import { CheckAllButton, CheckAllPanel, useCheckAllJob } from "../features/CheckAll";
import { ProblemBanner } from "../features/ProblemBanner";
import { displayName, profileUrl } from "../lib/people";
import { ago, fullTime, plural, untilText } from "../lib/time";

export function AccountsPage() {
  useTitle("Accounts");
  const { status, setup, now, openStatus } = useShell();
  const accounts = useAccounts();
  const channels = useChannels();
  const drawer = useAccountDrawer();
  const check = useCheckAllJob();
  const [params, setParams] = useSearchParams();

  useEffect(() => {
    if (params.get("add") === "1") {
      drawer.openAdd();
      setParams({}, { replace: true });
    }
  }, [params, setParams, drawer]);

  const list = accounts.data ?? [];
  const hasAccounts = list.length > 0;

  return (
    <>
      <div className="head">
        <div>
          <h1>Accounts</h1>
          <p>Bluesky accounts the bot watches. You get a Discord notification when they post something new.</p>
        </div>
        <div className="actions">
          {hasAccounts ? <CheckAllButton check={check} /> : null}
          <Button variant="primary" icon={Plus} onClick={drawer.openAdd}>
            Add account
          </Button>
        </div>
      </div>

      <ProblemBanner status={status} onMore={openStatus} />
      {status && !setup.complete && hasAccounts ? (
        <Banner tone="info" title="Setup isn't finished" actions={<Link className="btn sm" to="/setup">Continue setup</Link>}>
          {status.setup.missing.map((m) => m.message).join(" ")}
        </Banner>
      ) : null}

      {hasAccounts ? <Summary now={now} accounts={list} /> : null}
      <CheckAllPanel check={check} now={now} />

      {accounts.isPending ? (
        <section className="panel" aria-busy="true">
          <div className="panel-h">
            <h2>Watched accounts</h2>
          </div>
          <div className="scroll">
            <table>
              <tbody>
                <SkeletonRows columns={5} label="Loading the accounts…" />
              </tbody>
            </table>
          </div>
        </section>
      ) : accounts.isError && !accounts.data ? (
        <section className="panel">
          <Empty icon={Users} title="Couldn't load the accounts" tone="bad" action={<Button icon={RefreshCw} onClick={() => accounts.refetch()} busy={accounts.isFetching}>Try again</Button>}>
            {errorMessage(accounts.error)}
          </Empty>
        </section>
      ) : !hasAccounts ? (
        <section className="panel">
          <Empty icon={Users} title="No accounts yet" action={<Button variant="primary" icon={Plus} onClick={drawer.openAdd}>Add your first account</Button>}>
            Add any public Bluesky account: an artist, a news account, a creator. Their new posts will appear in your Discord channel.
          </Empty>
        </section>
      ) : (
        <div className="grid">
          <AccountsTable accounts={list} channels={channels.data ?? []} now={now} interval={status?.scheduler.check_interval_minutes} />
          <RecentActivity now={now} />
        </div>
      )}
    </>
  );
}

function Summary({ now, accounts }: { now: number; accounts: Account[] }) {
  const { status, view, openStatus } = useShell();
  // The last full check, or else the most recent single-account check (Check now, a first check).
  const lastAccountCheck = accounts.map((a) => a.last_checked_at).filter(Boolean).sort().pop() ?? null;
  const lastAt = [status?.scheduler.last_check?.finished_at, lastAccountCheck].filter(Boolean).sort().pop() ?? null;
  const problems = status?.problems.length ?? 0;
  const scheduler = status?.scheduler;
  return (
    <section className="summary" aria-label="Summary">
      <div>
        <div className="l">Status</div>
        <div className={`v ${view.tone === "ok" ? "ok" : view.tone === "warn" ? "warn" : view.tone === "bad" ? "bad" : ""}`}>
          <Dot tone={view.tone} />
          {view.label}
        </div>
        <div className="s">
          {scheduler ? `Checks every ${plural(scheduler.check_interval_minutes, "minute")}` : view.sub}
        </div>
      </div>
      <div>
        <div className="l">Last check</div>
        <div className="v num" title={fullTime(lastAt)}>
          {scheduler?.checking_now ? "Now" : lastAt ? ago(lastAt, now) : "Not yet"}
        </div>
        <div className="s">
          {scheduler?.checking_now
            ? "Checking now…"
            : scheduler?.next_check_at
              ? `Next ${untilText(scheduler.next_check_at, now)}`
              : view.offline
                ? "Not running"
                : "Waiting for the checker"}
        </div>
      </div>
      <div>
        <div className="l">Posts sent</div>
        <div className="v num">{status?.counts.sent_last_24h ?? "–"}</div>
        <div className="s">in the last 24 hours</div>
      </div>
      <div>
        <div className="l">Problems</div>
        <div className={`v num ${problems ? "bad" : ""}`}>{status ? problems : "–"}</div>
        <div className="s">
          {problems ? (
            <button type="button" className="link-btn" onClick={openStatus}>
              See what needs attention
            </button>
          ) : (
            "None right now"
          )}
        </div>
      </div>
    </section>
  );
}

function StateCell({ account }: { account: Account }) {
  switch (account.state) {
    case "checking":
      return <Badge tone="busy">Checking now</Badge>;
    case "paused":
      return <Badge tone="idle">Paused</Badge>;
    case "waiting_first_check":
      return (
        <>
          <Badge tone="warn">Waiting for first check</Badge>
          <span className="hint">Existing posts are skipped so Discord isn't flooded.</span>
        </>
      );
    case "error":
      return (
        <>
          <Badge tone={account.problem?.severity === "warning" ? "warn" : "bad"}>Problem</Badge>
          <span className="hint">{account.problem?.title ?? account.state_message}</span>
        </>
      );
    default:
      return <Badge tone="ok">Active</Badge>;
  }
}

function ChannelCell({ account, defaultChannel }: { account: Account; defaultChannel: Channel | undefined }) {
  if (account.channel) {
    return (
      <span className="chan">
        <Hash {...ICON} />
        {account.channel.name}
      </span>
    );
  }
  if (defaultChannel) {
    return (
      <span className="chan" title="Follows the default channel">
        <Hash {...ICON} />
        {defaultChannel.name}
        <small>default</small>
      </span>
    );
  }
  return <span className="chan" style={{ color: "var(--warn)" }}>No channel yet</span>;
}

function AccountsTable({ accounts, channels, now, interval }: { accounts: Account[]; channels: Channel[]; now: number; interval?: number }) {
  const drawer = useAccountDrawer();
  const toast = useToast();
  const update = useUpdateAccount();
  const remove = useRemoveAccount();
  const checkNow = useCheckNow();
  const [checking, setChecking] = useState<Set<number>>(new Set());
  const [removing, setRemoving] = useState<Account | null>(null);
  const defaultChannel = channels.find((c) => c.is_default);
  const active = accounts.filter((a) => a.is_active).length;

  const runCheck = (account: Account) => {
    const name = displayName(account);
    setChecking((s) => new Set(s).add(account.id));
    checkNow.mutate(account.id, {
      onSuccess: (result) => {
        if (result.error) toast(`Couldn't check ${name}: ${result.problem?.title ?? result.error}`, "bad");
        else if (result.was_first_poll)
          toast(`First check done for ${name}. Noted ${plural(result.first_check?.seeded_posts ?? 0, "existing post")}; none were sent.`);
        else if (result.check?.result === "failed") toast(`Couldn't check ${name}: ${result.check.message}`, "bad");
        else toast(`Checked ${name}. ${result.check?.message ?? ""}`.trim());
      },
      onError: (error) =>
        toast(error instanceof ApiError && error.code === "already_checking" ? `${name} is already being checked. Try again in a moment.` : errorMessage(error), "bad"),
      onSettled: () =>
        setChecking((s) => {
          const next = new Set(s);
          next.delete(account.id);
          return next;
        }),
    });
  };

  const toggle = (account: Account) => {
    const name = displayName(account);
    update.mutate(
      { id: account.id, is_active: !account.is_active },
      {
        onSuccess: () => toast(account.is_active ? `${name} paused. You won't get notifications for its new posts until you resume it.` : `${name} resumed.`),
        onError: (error) => toast(errorMessage(error), "bad"),
      },
    );
  };

  return (
    <section className="panel" aria-labelledby="accounts-title">
      <div className="panel-h">
        <div>
          <h2 id="accounts-title">Watched accounts</h2>
          <p className="num">
            {plural(accounts.length, "account")} · {active} active{interval ? ` · checked every ${plural(interval, "minute")}` : ""}
          </p>
        </div>
      </div>
      <div className="scroll">
        <table className="acc-table" role="table">
          <thead role="rowgroup">
            <tr role="row">
              <th scope="col" role="columnheader">Account</th>
              <th scope="col" role="columnheader">Posts to</th>
              <th scope="col" role="columnheader">Status</th>
              <th scope="col" role="columnheader">Last check</th>
              <th scope="col" role="columnheader">
                <span className="sr">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody role="rowgroup">
            {accounts.map((a) => {
              const name = displayName(a);
              const busy = checking.has(a.id) || a.checking_now;
              const togglingThis = update.isPending && update.variables?.id === a.id;
              return (
                <tr key={a.id} role="row" className={`${a.is_active ? "" : "paused"} ${drawer.freshId === a.id ? "fresh" : ""}`}>
                  <td role="cell" className="c-who">
                    <div className="who">
                      <Avatar name={name} handle={a.handle} />
                      <div style={{ minWidth: 0 }}>
                        <b>{name}</b>
                        <a href={profileUrl(a.handle)} target="_blank" rel="noopener noreferrer">
                          @{a.handle}
                        </a>
                      </div>
                    </div>
                  </td>
                  <td role="cell" className="c-chan">
                    <ChannelCell account={a} defaultChannel={defaultChannel} />
                  </td>
                  <td role="cell" className="c-state">
                    <StateCell account={a} />
                  </td>
                  <td role="cell" className="c-when when num">
                    <span title={fullTime(a.last_checked_at)}>
                      {a.last_checked_at ? (
                        <>
                          <span className="narrow-only">Checked </span>
                          {ago(a.last_checked_at, now)}
                        </>
                      ) : (
                        <>
                          Not <span className="narrow-only">checked </span>yet
                        </>
                      )}
                    </span>
                    {a.state === "error" && a.last_success_at ? <span className="hint">Last worked {ago(a.last_success_at, now)}</span> : null}
                  </td>
                  <td role="cell" className="c-acts">
                    <div className="row-acts">
                      <Button
                        size="sm"
                        variant="ghost"
                        icon={RefreshCw}
                        busy={busy}
                        disabled={!a.is_active}
                        title={a.is_active ? `Check ${name} now` : "Resume the account to check it"}
                        aria-label={`Check ${name} now`}
                        onClick={() => runCheck(a)}
                      >
                        <span className="lbl">Check now</span>
                      </Button>
                      {a.is_active ? (
                        <Button size="sm" variant="ghost" icon={Pause} busy={togglingThis} aria-label={`Pause ${name}`} title={`Pause ${name}`} onClick={() => toggle(a)}>
                          <span className="lbl">Pause</span>
                        </Button>
                      ) : (
                        <Button size="sm" icon={Play} busy={togglingThis} aria-label={`Resume ${name}`} title={`Resume ${name}`} onClick={() => toggle(a)}>
                          <span className="lbl">Resume</span>
                        </Button>
                      )}
                      <Menu
                        label={`More actions for ${name}`}
                        icon={MoreHorizontal}
                        items={[
                          { label: "Edit", icon: Pencil, onSelect: () => drawer.openEdit(a) },
                          { label: "Open on Bluesky", icon: ExternalLink, onSelect: () => window.open(profileUrl(a.handle), "_blank", "noopener,noreferrer") },
                          { label: "Remove", icon: Trash2, danger: true, onSelect: () => setRemoving(a) },
                        ]}
                      />
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <Confirm
        open={removing !== null}
        title={removing ? `Stop watching ${displayName(removing)}?` : ""}
        confirmLabel="Remove account"
        busy={remove.isPending}
        onCancel={() => setRemoving(null)}
        onConfirm={() => {
          if (!removing) return;
          const name = displayName(removing);
          remove.mutate(removing.id, {
            onSuccess: () => {
              toast(`Stopped watching ${name}.`);
              setRemoving(null);
            },
            onError: (error) => {
              toast(errorMessage(error), "bad");
              setRemoving(null);
            },
          });
        }}
      >
        {removing
          ? `The bot stops checking @${removing.handle}. Posts already sent to Discord stay in your channel. You can add the account again any time.`
          : null}
      </Confirm>
    </section>
  );
}

function RecentActivity({ now }: { now: number }) {
  const recent = useRecentActivity(6);
  const events = recent.data ?? [];
  return (
    <section className="panel" aria-labelledby="recent-title">
      <div className="panel-h">
        <h2 id="recent-title">Recent activity</h2>
        <Link to="/activity">View all</Link>
      </div>
      {recent.isPending ? (
        <SkeletonLines label="Loading recent activity…" />
      ) : recent.isError && !recent.data ? (
        <Empty icon={Activity} title="Couldn't load the activity" tone="bad">
          {errorMessage(recent.error)}
        </Empty>
      ) : events.length ? (
        <ul className="feed">
          {events.map((e) => (
            <ActivityItem key={e.id} event={e} now={now} />
          ))}
        </ul>
      ) : (
        <Empty icon={Activity} title="Nothing yet">
          When the bot sends a post or runs into a problem, it shows up here.
        </Empty>
      )}
    </section>
  );
}
