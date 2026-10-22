import { useEffect, useId, useState } from "react";
import { useLocation } from "react-router-dom";
import { Monitor, Moon, Sun } from "lucide-react";
import { errorMessage } from "../api/client";
import { useHealth, useSaveSettings, useSettings } from "../api/queries";
import { useTitle } from "../components/Shell";
import { Button, FieldError, ICON, SkeletonLines, useToast } from "../components/ui";
import { ChannelsCard } from "../features/Channels";
import { LoginForm } from "../features/LoginForm";
import { useTheme, type Theme } from "../lib/theme";

const INTERVALS = [1, 2, 3, 5, 10, 15, 20, 30, 45, 60];

export function SettingsPage() {
  useTitle("Settings");
  const settings = useSettings();
  const location = useLocation();

  useEffect(() => {
    // Links such as "Update the Bluesky login" open the matching section.
    if (!location.hash || settings.isPending) return;
    const target = document.getElementById(location.hash.slice(1));
    target?.scrollIntoView({ block: "start" });
    target?.querySelector<HTMLElement>("input, button, select")?.focus({ preventScroll: true });
  }, [location.hash, settings.isPending]);

  return (
    <>
      <div className="head">
        <div>
          <h1>Settings</h1>
          <p>Changes apply straight away. No restart needed.</p>
        </div>
      </div>

      <section className="sect" id="bluesky" aria-labelledby="s-bluesky">
        <div>
          <h2 id="s-bluesky">Bluesky login</h2>
          <p className="d">Your own Bluesky account. The bot needs it to read posts, even public ones. It's separate from the accounts you watch.</p>
        </div>
        {settings.isPending ? (
          <div className="card">
            <SkeletonLines label="Loading the settings…" />
          </div>
        ) : settings.isError && !settings.data ? (
          <div className="card">
            <FieldError>{errorMessage(settings.error)}</FieldError>
          </div>
        ) : (
          <LoginForm key={settings.data?.settings_version} settings={settings.data} mode="settings" />
        )}
      </section>

      <section className="sect" id="channels" aria-labelledby="s-channels">
        <div>
          <h2 id="s-channels">Discord channels</h2>
          <p className="d">Where notifications are posted. Each channel needs a webhook URL from Discord. The default channel is used unless an account picks another.</p>
        </div>
        <ChannelsCard />
      </section>

      <section className="sect" id="frequency" aria-labelledby="s-freq">
        <div>
          <h2 id="s-freq">Check frequency</h2>
          <p className="d">How often the bot looks for new posts on every active account. Every few minutes is plenty; Bluesky limits how often the bot may ask.</p>
        </div>
        {settings.data ? <FrequencyForm current={Number(settings.data.poll_interval_minutes)} /> : <div className="card"><SkeletonLines lines={1} label="Loading…" /></div>}
      </section>

      <section className="sect" id="appearance" aria-labelledby="s-look">
        <div>
          <h2 id="s-look">Appearance</h2>
          <p className="d">Light or dark. "Same as this computer" follows your Windows setting.</p>
        </div>
        <div className="card">
          <ThemePicker />
        </div>
      </section>

      <section className="sect" id="about" aria-labelledby="s-about">
        <div>
          <h2 id="s-about">About this bot</h2>
          <p className="d">Where your data is and how to keep a copy.</p>
        </div>
        <About />
      </section>
    </>
  );
}

function FrequencyForm({ current }: { current: number }) {
  const id = useId();
  const toast = useToast();
  const save = useSaveSettings();
  const [value, setValue] = useState(current);
  const [error, setError] = useState("");
  useEffect(() => setValue(current), [current]); // a change saved elsewhere shows up here
  const options = INTERVALS.includes(current) ? INTERVALS : [...INTERVALS, current].sort((a, b) => a - b);
  return (
    <form
      className="card"
      onSubmit={(event) => {
        event.preventDefault();
        setError("");
        save.mutate(
          { poll_interval_minutes: value },
          {
            onSuccess: () => toast(`Saved. The bot now checks every ${value} minute${value === 1 ? "" : "s"}.`),
            onError: (e) => setError(errorMessage(e)),
          },
        );
      }}
    >
      <div className="field" style={{ maxWidth: 240 }}>
        <label htmlFor={id}>Check every</label>
        <select id={id} className="select" value={value} onChange={(e) => setValue(Number(e.target.value))}>
          {options.map((n) => (
            <option key={n} value={n}>
              {n} minute{n === 1 ? "" : "s"}
              {n === 5 ? " (recommended)" : ""}
            </option>
          ))}
        </select>
      </div>
      <FieldError>{error}</FieldError>
      <div className="form-foot">
        <Button variant="primary" type="submit" busy={save.isPending} busyLabel="Saving…" disabled={value === current}>
          Save
        </Button>
        {value === current ? <span className="muted" style={{ fontSize: 13 }}>Currently every {current} minute{current === 1 ? "" : "s"}.</span> : null}
      </div>
    </form>
  );
}

function ThemePicker() {
  const [theme, setTheme] = useTheme();
  const options: { value: Theme; label: string; icon: typeof Sun }[] = [
    { value: "system", label: "Same as this computer", icon: Monitor },
    { value: "light", label: "Light", icon: Sun },
    { value: "dark", label: "Dark", icon: Moon },
  ];
  return (
    <div className="seg theme-seg" role="group" aria-label="Appearance">
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={theme === o.value} onClick={() => setTheme(o.value)}>
          <o.icon {...ICON} />
          {o.label}
        </button>
      ))}
    </div>
  );
}

function About() {
  const health = useHealth();
  return (
    <div className="card">
      <dl className="about">
        <dt>Version</dt>
        <dd>{health.data?.version ?? "…"}</dd>
        <dt>Your data</dt>
        <dd>Accounts, settings and history are stored on this computer, inside Docker Desktop. Nothing is sent anywhere except the posts to your Discord channels.</dd>
        <dt>Backups</dt>
        <dd>
          A backup is made automatically before every update. To save a copy yourself, double-click <b>backup.bat</b> in the bot's folder; it goes into the <b>backups</b> folder there. Backups contain your App Password and webhook URLs, so keep them private.
        </dd>
        <dt>Updates</dt>
        <dd>
          Double-click <b>update.bat</b>. It backs up first, then installs the new version.
        </dd>
        <dt>License</dt>
        <dd>
          Free and open source under the MIT License. The bot includes other open-source software, under its own licenses:{" "}
          <a href="/third-party-notices.txt" target="_blank" rel="noopener noreferrer">
            third-party notices
          </a>
          .
        </dd>
      </dl>
    </div>
  );
}
