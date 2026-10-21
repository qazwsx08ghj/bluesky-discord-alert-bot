import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { Activity, ListChecks, Plug, RefreshCw, Send, SlidersHorizontal, Users } from "lucide-react";
import type { UseQueryResult } from "@tanstack/react-query";
import { useHealth, useChannels, useStatus } from "../api/queries";
import type { BotStatus } from "../api/types";
import { StatusDialog } from "../features/StatusDialog";
import { statusView, type StatusView } from "../lib/botStatus";
import { setupProgress, type SetupProgress } from "../lib/setup";
import { recall, remember } from "../lib/storage";
import { useNow } from "../lib/time";
import { Banner, Button, Dot, ICON } from "./ui";

interface ShellState {
  status: BotStatus | undefined;
  statusQuery: UseQueryResult<BotStatus, unknown>;
  setup: SetupProgress;
  view: StatusView;
  now: number;
  openStatus: () => void;
  /** Setup step 3 ("Send a test message") was skipped in this browser. */
  skipTest: () => void;
}

const ShellContext = createContext<ShellState | null>(null);

export function useShell(): ShellState {
  const value = useContext(ShellContext);
  if (!value) throw new Error("useShell outside the shell");
  return value;
}

/** Sets the browser tab's title for the current page. */
export function useTitle(title: string) {
  useEffect(() => {
    document.title = `${title} · Alert Bot`;
  }, [title]);
}

function NavItem({ to, icon: Icon, label, count, countTone }: { to: string; icon: typeof Users; label: string; count?: ReactNode; countTone?: "bad" }) {
  return (
    <NavLink to={to} className="nav" aria-label={typeof count === "number" ? `${label} (${count}${countTone === "bad" ? " problems" : ""})` : label}>
      <Icon {...ICON} />
      <span className="label">{label}</span>
      {count !== undefined && count !== null && count !== 0 ? <span className={`count num ${countTone ?? ""}`}>{count}</span> : null}
    </NavLink>
  );
}

export function Shell() {
  const statusQuery = useStatus();
  const channels = useChannels();
  const health = useHealth();
  const now = useNow(15_000);
  const status = statusQuery.data;
  const [testSkipped, setTestSkipped] = useState(() => recall("test-skipped") === "1");
  const setup = setupProgress(status, channels.data, testSkipped);
  // While offline the last good answer is still shown underneath, dimmed.
  const view = statusView(status, statusQuery.error, setup, now);
  const [statusOpen, setStatusOpen] = useState(false);

  const location = useLocation();
  const first = useRef(true);
  useEffect(() => {
    // Move focus to the page that was opened, so keyboard and screen reader users start there.
    if (first.current) {
      first.current = false;
      return;
    }
    document.getElementById("main")?.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }, [location.pathname]);

  const showSetup = Boolean(status) && !setup.complete;
  const problems = status?.problems.length ?? 0;

  return (
    <ShellContext.Provider
      value={{
        status,
        statusQuery,
        setup,
        view,
        now,
        openStatus: () => setStatusOpen(true),
        skipTest: () => {
          remember("test-skipped", "1");
          setTestSkipped(true);
        },
      }}
    >
      <div className={`app ${view.offline ? "offline" : ""}`}>
        <a href="#main" className="skip">
          Skip to content
        </a>
        <nav className="side" aria-label="Main">
          <div className="brand">
            <div className="mark" aria-hidden="true">
              <Send size={15} strokeWidth={1.75} className="icon" />
            </div>
            <div>
              <b>Alert Bot</b>
              <span>Bluesky → Discord</span>
            </div>
          </div>
          {showSetup ? <NavItem to="/setup" icon={ListChecks} label="Setup" count={`${setup.done}/4`} /> : null}
          <NavItem to="/accounts" icon={Users} label="Accounts" count={status?.counts.accounts_total} />
          <NavItem to="/activity" icon={Activity} label="Activity" count={problems || undefined} countTone="bad" />
          <NavItem to="/settings" icon={SlidersHorizontal} label="Settings" />
          {showSetup && location.pathname !== "/setup" ? (
            <NavLink to="/setup" className="setup-link">
              <b>Finish setup</b>
              <span>
                {setup.done} of 4 steps done
              </span>
              <div className="bar" aria-hidden="true">
                <i style={{ width: `${setup.done * 25}%` }} />
              </div>
            </NavLink>
          ) : null}
          <div className="side-foot">
            <button type="button" className="status-btn" onClick={() => setStatusOpen(true)} aria-label={`Bot status: ${view.label}. ${view.sub}. Show details`}>
              <span className="st">
                <Dot tone={view.tone} />
                {view.label}
              </span>
              <small>{view.sub}</small>
            </button>
          </div>
        </nav>
        <main id="main" tabIndex={-1}>
          <div className="wrap page">
            {view.offline ? (
              <Banner
                tone="bad"
                icon={Plug}
                title="Can't reach the bot"
                actions={
                  <Button size="sm" icon={RefreshCw} onClick={() => statusQuery.refetch()} busy={statusQuery.isFetching} busyLabel="Trying…">
                    Try again
                  </Button>
                }
              >
                Make sure Docker Desktop is open, then double-click <b>start.bat</b>. This page reconnects by itself.
              </Banner>
            ) : null}
            <Outlet />
          </div>
        </main>
        <StatusDialog open={statusOpen} onClose={() => setStatusOpen(false)} status={status} view={view} version={health.data?.version} now={now} />
      </div>
    </ShellContext.Provider>
  );
}
