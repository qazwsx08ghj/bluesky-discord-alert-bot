import { useId, useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import { ApiError, errorMessage } from "../api/client";
import { useSaveSettings, useTestLogin } from "../api/queries";
import type { LoginTest, Settings } from "../api/types";
import { Button, Dot, FieldError, FieldOk, FieldWarn, IconButton, useToast } from "../components/ui";
import { fullTime } from "../lib/time";

export function AppPasswordHowTo() {
  return (
    <details className="howto">
      <summary>How do I get an App Password?</summary>
      <ol>
        <li>Open bsky.app and sign in to your own account.</li>
        <li>Go to Settings → Privacy and Security → App Passwords.</li>
        <li>Choose Add App Password, name it "discord-bot", and copy it.</li>
        <li>Paste it here. You can delete it on Bluesky at any time to cut the bot off.</li>
      </ol>
    </details>
  );
}

function LoginState({ settings }: { settings: Settings }) {
  const login = settings.bluesky_login;
  switch (login.state) {
    case "ok":
      return (
        <div className="conn">
          <Dot tone="ok" />
          Connected as <b>@{login.handle}</b>
        </div>
      );
    case "failed":
      return (
        <div className="conn" style={{ background: "var(--bad-bg)", color: "var(--bad)" }} role="alert">
          <Dot tone="bad" />
          <span>
            <b>Bluesky didn't accept this login.</b> The App Password may have been deleted. Create a new one and paste it below.
          </span>
        </div>
      );
    case "rate_limited":
      return (
        <div className="conn" style={{ background: "var(--warn-bg)", color: "var(--warn)" }}>
          <Dot tone="warn" />
          <span>Bluesky asked the bot to wait before signing in again{login.retry_after ? ` (until ${fullTime(login.retry_after)})` : ""}. It tries again by itself.</span>
        </div>
      );
    case "unknown":
      return (
        <div className="conn">
          <Dot tone="idle" />
          Saved. The next check signs in as <b>@{login.handle}</b>.
        </div>
      );
    default:
      return (
        <div className="conn">
          <Dot tone="idle" />
          Not set up yet. The bot needs this login to read posts.
        </div>
      );
  }
}

function TestResult({ result }: { result: LoginTest }) {
  if (result.ok) {
    return (
      <>
        <FieldOk>{result.message}</FieldOk>
        {result.warning ? <FieldWarn>{result.warning}</FieldWarn> : null}
      </>
    );
  }
  if (result.result === "rate_limited" || result.result === "unavailable" || result.result === "error") {
    return <FieldWarn>{result.message}</FieldWarn>;
  }
  return <FieldError>{result.problem?.explanation && result.result !== "rejected" ? result.problem.explanation : result.message}</FieldError>;
}

/** The bot's own Bluesky login. `setup`: one "Test and save" button; `settings`: Save and Test. */
export function LoginForm({ settings, mode, onSaved }: { settings: Settings | undefined; mode: "setup" | "settings"; onSaved?: () => void }) {
  const id = useId();
  const toast = useToast();
  const save = useSaveSettings();
  const test = useTestLogin();
  const [handle, setHandle] = useState(settings?.bluesky_handle ?? "");
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
  const [result, setResult] = useState<LoginTest | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState("");
  const [saved, setSaved] = useState(false);
  const hasPassword = Boolean(settings?.has_bluesky_password);

  const values = () => ({ bluesky_handle: handle.trim().replace(/^@/, ""), bluesky_password: password.trim() });

  const check = (): boolean => {
    const next: Record<string, string> = {};
    if (!values().bluesky_handle) next.bluesky_handle = "Enter the handle of your own Bluesky account, for example yourname.bsky.social.";
    if (!values().bluesky_password && !hasPassword) next.bluesky_password = "Paste an App Password from Bluesky (see below how to make one).";
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  async function doSave() {
    setFormError("");
    setSaved(false);
    try {
      const body = values();
      await save.mutateAsync(body.bluesky_password ? body : { bluesky_handle: body.bluesky_handle });
      setPassword("");
      setSaved(true);
      if (mode === "settings") toast("Bluesky login saved. The next check uses it.");
      onSaved?.();
    } catch (error) {
      if (error instanceof ApiError && Object.keys(error.fields).length) {
        setErrors(Object.fromEntries(Object.entries(error.fields).map(([k, v]) => [k, v.join(" ")])));
      } else setFormError(errorMessage(error));
    }
  }

  async function doTest(): Promise<LoginTest | null> {
    setFormError("");
    setResult(null);
    try {
      const answer = await test.mutateAsync(values());
      setResult(answer);
      if (answer.fields) setErrors(Object.fromEntries(Object.entries(answer.fields).map(([k, v]) => [k, v.join(" ")])));
      return answer;
    } catch (error) {
      setFormError(errorMessage(error));
      return null;
    }
  }

  async function testAndSave() {
    if (!check()) return;
    const answer = await doTest();
    if (answer?.ok) await doSave();
  }

  const unsure = result && ["rate_limited", "unavailable", "error"].includes(result.result);

  return (
    <form
      className={mode === "settings" ? "card" : undefined}
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        if (mode === "setup") testAndSave();
        else if (check()) doSave();
      }}
    >
      {mode === "settings" && settings ? <LoginState settings={settings} /> : null}
      <div className="row2">
        <div className="field">
          <label htmlFor={`${id}-h`}>Your Bluesky handle</label>
          <input
            id={`${id}-h`}
            className="input"
            value={handle}
            placeholder="yourname.bsky.social"
            autoComplete="username"
            spellCheck={false}
            aria-invalid={Boolean(errors.bluesky_handle) || undefined}
            aria-describedby={errors.bluesky_handle ? `${id}-he` : undefined}
            onChange={(e) => {
              setHandle(e.target.value);
              setResult(null);
              setSaved(false);
            }}
          />
          <FieldError id={`${id}-he`}>{errors.bluesky_handle}</FieldError>
        </div>
        <div className="field">
          <label htmlFor={`${id}-p`}>App Password</label>
          <div style={{ display: "flex", gap: 6 }}>
            <input
              id={`${id}-p`}
              className="input mono"
              type={reveal ? "text" : "password"}
              value={password}
              placeholder={hasPassword ? "Saved. Type a new one to replace it" : "xxxx-xxxx-xxxx-xxxx"}
              autoComplete="new-password"
              spellCheck={false}
              aria-invalid={Boolean(errors.bluesky_password) || undefined}
              aria-describedby={errors.bluesky_password ? `${id}-pe` : undefined}
              onChange={(e) => {
                setPassword(e.target.value);
                setResult(null);
                setSaved(false);
              }}
            />
            <IconButton icon={reveal ? EyeOff : Eye} label={reveal ? "Hide the App Password" : "Show the App Password"} size="md" variant="default" onClick={() => setReveal((v) => !v)} />
          </div>
          <FieldError id={`${id}-pe`}>{errors.bluesky_password}</FieldError>
        </div>
      </div>
      <AppPasswordHowTo />
      <div aria-live="polite" style={{ display: "grid", gap: 6, marginBottom: result || formError ? 14 : 0 }}>
        {result ? <TestResult result={result} /> : null}
        <FieldError>{formError}</FieldError>
      </div>
      <div className="form-foot">
        {mode === "setup" ? (
          <>
            <Button variant="primary" type="submit" busy={test.isPending || save.isPending} busyLabel={test.isPending ? "Testing…" : "Saving…"}>
              Test and continue
            </Button>
            {unsure ? (
              <Button onClick={doSave} busy={save.isPending}>
                Save without testing
              </Button>
            ) : null}
          </>
        ) : (
          <>
            <Button variant="primary" type="submit" busy={save.isPending} busyLabel="Saving…">
              Save login
            </Button>
            <Button onClick={() => check() && doTest()} busy={test.isPending} busyLabel="Testing…">
              Test login
            </Button>
            {saved ? <span className="saved">Saved. Used from the next check.</span> : null}
          </>
        )}
      </div>
      <p className="muted" style={{ fontSize: 12, marginTop: 12 }}>
        The App Password stays on this computer. It's never shown again, not even here.
      </p>
    </form>
  );
}
