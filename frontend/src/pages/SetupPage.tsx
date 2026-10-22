import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Check, CircleCheck, Plus, Send } from "lucide-react";
import { useChannels, useSettings, useTestChannel } from "../api/queries";
import { useShell, useTitle } from "../components/Shell";
import { Banner, Button, FieldError, ICON, SkeletonLines, useToast } from "../components/ui";
import { useAccountDrawer } from "../features/AccountDrawer";
import { FirstChannelForm } from "../features/Channels";
import { LoginForm } from "../features/LoginForm";

function Step({
  n,
  title,
  sub,
  done,
  open,
  onChange,
  children,
}: {
  n: number;
  title: string;
  sub: string;
  done: boolean;
  open: boolean;
  onChange: () => void;
  children: ReactNode;
}) {
  return (
    <li className={`step ${done && !open ? "done" : ""} ${open ? "current" : ""}`} aria-current={open ? "step" : undefined}>
      <div className="step-h">
        <div className="step-n num" aria-hidden="true">
          {done && !open ? <Check size={14} strokeWidth={2.25} /> : n}
        </div>
        <div>
          <h2>
            <span className="sr">Step {n}: </span>
            {title}
          </h2>
          <p>{done && !open ? "Done" : sub}</p>
        </div>
        {done && !open ? (
          <Button size="sm" variant="ghost" className="r" onClick={onChange} aria-label={`Change: ${title}`}>
            Change
          </Button>
        ) : null}
      </div>
      {open ? <div className="step-b">{children}</div> : null}
    </li>
  );
}

export function SetupPage() {
  useTitle("Setup");
  const { setup, statusQuery, skipTest } = useShell();
  const settings = useSettings();
  const channels = useChannels();
  const drawer = useAccountDrawer();
  const test = useTestChannel();
  const toast = useToast();
  const [reopened, setReopened] = useState<number | null>(null);
  const [testError, setTestError] = useState("");
  const defaultChannel = channels.data?.find((c) => c.is_default);
  const current = reopened ?? setup.current;
  const allDone = setup.current === 5 && reopened === null;

  if (statusQuery.isPending || channels.isPending) {
    return (
      <div className="head">
        <div>
          <h1>Set up your bot</h1>
          <p>Loading…</p>
        </div>
      </div>
    );
  }

  const finishStep = () => setReopened(null);

  return (
    <>
      <div className="head">
        <div>
          <h1>Set up your bot</h1>
          <p className="num">
            {setup.done} of 4 done. About five minutes in total. You can leave and come back; everything is saved as you go.
          </p>
        </div>
      </div>
      {allDone ? (
        <Banner tone="info" icon={CircleCheck} title="Your bot is set up" actions={<Link className="btn sm primary" to="/accounts">Go to Accounts</Link>}>
          It checks your accounts every few minutes and posts anything new to Discord. Add more accounts any time.
        </Banner>
      ) : null}
      <ol className="steps">
        <Step n={1} title="Connect your Bluesky login" sub="Your own account, used to read posts" done={setup.login} open={current === 1} onChange={() => setReopened(1)}>
          <p className="why">
            The bot needs a Bluesky login to read posts, even public ones. Use an <b>App Password</b>, not your real password: it can't change your password or account settings, and you can delete it on Bluesky any time.
          </p>
          {/* Wait for the saved settings: the form starts from them, and must not be rebuilt (losing what
              was typed) when they arrive after the user has started typing. */}
          {settings.isPending ? (
            <SkeletonLines label="Loading the settings…" />
          ) : (
            <LoginForm key={settings.data?.settings_version ?? 0} settings={settings.data} mode="setup" onSaved={finishStep} />
          )}
        </Step>
        <Step n={2} title="Choose a Discord channel" sub="Where notifications go" done={setup.channel} open={current === 2} onChange={() => setReopened(2)}>
          <p className="why">Discord gives each channel a webhook URL that lets the bot post there. You only need one to start; you can add more later in Settings.</p>
          <FirstChannelForm existing={defaultChannel} onSaved={finishStep} />
        </Step>
        <Step n={3} title="Send a test message" sub="Check that Discord receives it" done={setup.tested} open={current === 3} onChange={() => setReopened(3)}>
          <p className="why">
            This posts a sample notification to {defaultChannel ? <b>#{defaultChannel.name}</b> : "your channel"} so you can see what they look like.
          </p>
          <div aria-live="polite" style={{ marginBottom: testError ? 12 : 0 }}>
            <FieldError>{testError}</FieldError>
          </div>
          <div className="actions">
            <Button
              variant="primary"
              icon={Send}
              busy={test.isPending}
              busyLabel="Sending…"
              disabled={!defaultChannel}
              onClick={() => {
                if (!defaultChannel) return;
                setTestError("");
                test.mutate(defaultChannel.id, {
                  onSuccess: () => {
                    toast(`Test message sent. Check #${defaultChannel.name} in Discord.`);
                    finishStep();
                  },
                  onError: () =>
                    setTestError(
                      "Discord didn't accept the test message. The webhook URL may be incomplete or the webhook was deleted. Copy it again from Discord and paste it in step 2.",
                    ),
                });
              }}
            >
              Send test message
            </Button>
            {testError ? (
              <Button onClick={() => setReopened(2)}>Change the webhook URL</Button>
            ) : (
              <Button
                variant="ghost"
                onClick={() => {
                  skipTest();
                  finishStep();
                }}
              >
                Skip for now
              </Button>
            )}
          </div>
        </Step>
        <Step n={4} title="Add your first account" sub="Choose whose posts to follow" done={setup.account} open={current === 4} onChange={() => setReopened(4)}>
          <p className="why">Any public Bluesky account works: artists, news, creators. It doesn't have to be yours.</p>
          <div className="actions">
            <Button variant="primary" icon={Plus} onClick={drawer.openAdd}>
              Add account
            </Button>
            {setup.account ? (
              <Link className="btn" to="/accounts">
                <Check {...ICON} />
                See your accounts
              </Link>
            ) : null}
          </div>
        </Step>
      </ol>
    </>
  );
}
