import { useId, useState } from "react";
import { Hash, MoreHorizontal, Pencil, Plus, Send, Star, Trash2 } from "lucide-react";
import { ApiError, errorMessage } from "../api/client";
import { useAddChannel, useChannels, useMakeDefault, useRemoveChannel, useTestChannel, useUpdateChannel } from "../api/queries";
import type { Channel } from "../api/types";
import { Button, Dialog, Empty, FieldError, ICON, IconButton, Menu, SkeletonLines, useToast } from "../components/ui";
import { ago, fullTime, plural } from "../lib/time";
import { useShell } from "../components/Shell";

export function WebhookHowTo({ open }: { open?: boolean }) {
  return (
    <details className="howto" open={open}>
      <summary>Where do I find a webhook URL?</summary>
      <ol>
        <li>In Discord, hover the channel and click the gear icon (Edit Channel).</li>
        <li>Open Integrations → Webhooks → New Webhook.</li>
        <li>Name it "Bluesky Bot" and choose Copy Webhook URL.</li>
        <li>Paste it here. Anyone with this URL can post in the channel, so keep it private.</li>
      </ol>
    </details>
  );
}

type Errors = Record<string, string>;
const toErrors = (error: unknown): [Errors, string] => {
  if (error instanceof ApiError && Object.keys(error.fields).length) {
    return [Object.fromEntries(Object.entries(error.fields).map(([k, v]) => [k, v.join(" ")])), ""];
  }
  return [{}, errorMessage(error)];
};

/** Name and webhook URL. Used by the setup step and the add/edit dialog. */
export function ChannelFields({
  name,
  url,
  onName,
  onUrl,
  errors,
  editing,
  idPrefix,
}: {
  name: string;
  url: string;
  onName: (v: string) => void;
  onUrl: (v: string) => void;
  errors: Errors;
  editing?: Channel;
  idPrefix: string;
}) {
  return (
    <div className="row2">
      <div className="field">
        <label htmlFor={`${idPrefix}-n`}>Channel name</label>
        <input id={`${idPrefix}-n`} className="input" value={name} placeholder="announcements" maxLength={100} aria-invalid={Boolean(errors.name) || undefined} onChange={(e) => onName(e.target.value)} />
        <span className="help">Just a label for you, usually the Discord channel's name.</span>
        <FieldError>{errors.name}</FieldError>
      </div>
      <div className="field">
        <label htmlFor={`${idPrefix}-u`}>Webhook URL</label>
        <input
          id={`${idPrefix}-u`}
          className="input mono"
          value={url}
          placeholder={editing ? "Saved. Paste a new URL to replace it" : "https://discord.com/api/webhooks/…"}
          autoComplete="off"
          spellCheck={false}
          aria-invalid={Boolean(errors.url) || undefined}
          onChange={(e) => onUrl(e.target.value)}
        />
        {editing ? <span className="help">Saved as {editing.url_masked}. Leave empty to keep it.</span> : null}
        <FieldError>{errors.url}</FieldError>
      </div>
    </div>
  );
}

/** Setup step 2: the first channel, which becomes the default. */
export function FirstChannelForm({ existing, onSaved }: { existing: Channel | undefined; onSaved: () => void }) {
  const id = useId();
  const add = useAddChannel();
  const update = useUpdateChannel();
  const [name, setName] = useState(existing?.name ?? "");
  const [url, setUrl] = useState("");
  const [errors, setErrors] = useState<Errors>({});
  const [formError, setFormError] = useState("");
  return (
    <form
      noValidate
      onSubmit={async (event) => {
        event.preventDefault();
        setErrors({});
        setFormError("");
        const local: Errors = {};
        if (!name.trim()) local.name = "Give the channel a name, for example announcements.";
        if (!url.trim() && !existing) local.url = "Paste the webhook URL from Discord.";
        if (Object.keys(local).length) return setErrors(local);
        try {
          if (existing) await update.mutateAsync({ id: existing.id, name: name.trim(), ...(url.trim() ? { url: url.trim() } : {}), is_default: true });
          else await add.mutateAsync({ name: name.trim(), url: url.trim(), is_default: true });
          onSaved();
        } catch (error) {
          const [fields, message] = toErrors(error);
          setErrors(fields);
          setFormError(message);
        }
      }}
    >
      <ChannelFields idPrefix={id} name={name} url={url} onName={setName} onUrl={setUrl} errors={errors} editing={existing} />
      <WebhookHowTo open={!existing} />
      <div style={{ marginBottom: formError ? 12 : 0 }}>
        <FieldError>{formError}</FieldError>
      </div>
      <Button variant="primary" type="submit" busy={add.isPending || update.isPending} busyLabel="Saving…">
        Save and continue
      </Button>
    </form>
  );
}

function ChannelDialog({ channel, open, onClose, isFirst }: { channel: Channel | null; open: boolean; onClose: () => void; isFirst: boolean }) {
  const id = useId();
  return (
    <Dialog open={open} onClose={onClose} labelledBy={`${id}-t`} className="modal wide">
      {open ? <ChannelDialogBody key={channel?.id ?? "new"} channel={channel} titleId={`${id}-t`} onClose={onClose} isFirst={isFirst} /> : null}
    </Dialog>
  );
}

function ChannelDialogBody({ channel, titleId, onClose, isFirst }: { channel: Channel | null; titleId: string; onClose: () => void; isFirst: boolean }) {
  const id = useId();
  const toast = useToast();
  const add = useAddChannel();
  const update = useUpdateChannel();
  const [name, setName] = useState(channel?.name ?? "");
  const [url, setUrl] = useState("");
  const [makeDefault, setMakeDefault] = useState(channel?.is_default ?? isFirst);
  const [errors, setErrors] = useState<Errors>({});
  const [formError, setFormError] = useState("");
  const busy = add.isPending || update.isPending;
  return (
    <form
      noValidate
      onSubmit={async (event) => {
        event.preventDefault();
        setErrors({});
        setFormError("");
        const local: Errors = {};
        if (!name.trim()) local.name = "Give the channel a name.";
        if (!channel && !url.trim()) local.url = "Paste the webhook URL from Discord.";
        if (Object.keys(local).length) return setErrors(local);
        try {
          if (channel) {
            await update.mutateAsync({ id: channel.id, name: name.trim(), ...(url.trim() ? { url: url.trim() } : {}), ...(makeDefault && !channel.is_default ? { is_default: true } : {}) });
            toast(`Saved #${name.trim()}.`);
          } else {
            await add.mutateAsync({ name: name.trim(), url: url.trim(), is_default: makeDefault });
            toast(`Added #${name.trim()}. Send a test message to check it works.`);
          }
          onClose();
        } catch (error) {
          const [fields, message] = toErrors(error);
          setErrors(fields);
          setFormError(message);
        }
      }}
    >
      <h2 id={titleId}>{channel ? `Edit #${channel.name}` : "Add a Discord channel"}</h2>
      <p className="lead">Each channel needs its own webhook URL from Discord. Accounts can post to the default channel or pick their own.</p>
      <ChannelFields idPrefix={id} name={name} url={url} onName={setName} onUrl={setUrl} errors={errors} editing={channel ?? undefined} />
      <WebhookHowTo />
      <label className="opt" style={{ marginBottom: 0 }}>
        <input type="checkbox" checked={makeDefault} disabled={channel?.is_default || isFirst} onChange={(e) => setMakeDefault(e.target.checked)} />
        <span>
          <b>Default channel</b>
          <small>{channel?.is_default ? "This is the default. To change it, make another channel the default." : "Accounts that don't pick a channel post here."}</small>
        </span>
      </label>
      <div style={{ marginTop: 12 }}>
        <FieldError>{formError}</FieldError>
      </div>
      <div className="f">
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button variant="primary" type="submit" busy={busy} busyLabel="Saving…">
          {channel ? "Save changes" : "Add channel"}
        </Button>
      </div>
    </form>
  );
}

function DeleteChannelDialog({ channel, channels, onClose }: { channel: Channel | null; channels: Channel[]; onClose: () => void }) {
  const id = useId();
  const toast = useToast();
  const remove = useRemoveChannel();
  const others = channels.filter((c) => c.id !== channel?.id);
  const defaultChannel = channels.find((c) => c.is_default);
  const [target, setTarget] = useState<string>("default");
  const [error, setError] = useState("");
  const inUse = (channel?.account_count ?? 0) > 0;
  const blocked = channel?.is_default;

  return (
    <Dialog open={channel !== null} onClose={remove.isPending ? () => undefined : onClose} labelledBy={`${id}-t`} describedBy={`${id}-d`} className="modal" role="alertdialog">
      {channel ? (
        <>
          <h2 id={`${id}-t`}>{blocked ? `#${channel.name} is the default channel` : `Delete #${channel.name}?`}</h2>
          <div className="lead" id={`${id}-d`}>
            {blocked ? (
              others.length ? (
                "Make another channel the default first (More actions → Make default), then delete this one."
              ) : (
                "It's the only channel, so the bot needs it. Add another channel and make it the default first."
              )
            ) : (
              <>
                The bot stops posting to this channel and forgets its webhook URL. Messages already in Discord stay there.
                {inUse ? ` ${plural(channel.account_count, "account")} post${channel.account_count === 1 ? "s" : ""} here.` : ""}
              </>
            )}
          </div>
          {!blocked && inUse ? (
            <div className="field">
              <label htmlFor={`${id}-to`}>Move {channel.account_count === 1 ? "it" : "them"} to</label>
              <select id={`${id}-to`} className="select" value={target} onChange={(e) => setTarget(e.target.value)}>
                <option value="default">The default channel{defaultChannel ? ` (#${defaultChannel.name})` : ""}</option>
                {others
                  .filter((c) => !c.is_default)
                  .map((c) => (
                    <option key={c.id} value={c.id}>
                      #{c.name}
                    </option>
                  ))}
              </select>
            </div>
          ) : null}
          <FieldError>{error}</FieldError>
          <div className="f">
            <Button onClick={onClose} disabled={remove.isPending} data-autofocus>
              {blocked ? "OK" : "Cancel"}
            </Button>
            {blocked ? null : (
              <Button
                variant="danger"
                busy={remove.isPending}
                busyLabel="Deleting…"
                onClick={() => {
                  setError("");
                  remove.mutate(
                    { id: channel.id, reassignTo: inUse ? (target === "default" ? "default" : Number(target)) : undefined },
                    {
                      onSuccess: () => {
                        toast(`Deleted #${channel.name}.`);
                        onClose();
                      },
                      onError: (e) => setError(errorMessage(e)),
                    },
                  );
                }}
              >
                Delete channel
              </Button>
            )}
          </div>
        </>
      ) : null}
    </Dialog>
  );
}

/** Settings → Discord channels. */
export function ChannelsCard() {
  const toast = useToast();
  const channels = useChannels();
  const test = useTestChannel();
  const makeDefault = useMakeDefault();
  const [editing, setEditing] = useState<Channel | null>(null);
  const [adding, setAdding] = useState(false);
  const [deleting, setDeleting] = useState<Channel | null>(null);
  const { now } = useShell();
  const list = [...(channels.data ?? [])].sort((a, b) => Number(b.is_default) - Number(a.is_default) || a.name.localeCompare(b.name));

  const sendTest = (c: Channel) =>
    test.mutate(c.id, {
      onSuccess: () => toast(`Test message sent. Check #${c.name} in Discord.`),
      onError: () => toast(`Discord didn't accept the test message for #${c.name}. Check the webhook URL, or make a new webhook and paste it here.`, "bad"),
    });

  return (
    <div className="card">
      {channels.isPending ? (
        <SkeletonLines label="Loading the channels…" />
      ) : channels.isError && !channels.data ? (
        <FieldError>{errorMessage(channels.error)}</FieldError>
      ) : list.length ? (
        <div className="chan-list">
          {list.map((c) => (
            <div className="chan-row" key={c.id}>
              <div style={{ minWidth: 0, flex: "1 1 260px" }}>
                <div className="n">
                  <Hash {...ICON} />
                  {c.name}
                  {c.is_default ? <span className="tag">Default</span> : null}
                </div>
                <div className="u">{c.url_masked}</div>
                <div className="meta">
                  <span>{c.is_default ? "Used by accounts without their own channel" : plural(c.account_count, "account")}</span>
                  {c.last_delivery ? (
                    <span title={fullTime(c.last_delivery.at)} className={c.last_delivery.ok ? "" : "bad"}>
                      · {c.last_delivery.ok ? `last message delivered ${ago(c.last_delivery.at, now)}` : `last message failed ${ago(c.last_delivery.at, now)}`}
                    </span>
                  ) : (
                    <span>· nothing sent yet</span>
                  )}
                </div>
              </div>
              <div className="acts">
                <Button size="sm" icon={Send} busy={test.isPending && test.variables === c.id} busyLabel="Sending…" onClick={() => sendTest(c)} aria-label={`Send a test message to #${c.name}`}>
                  Send test
                </Button>
                <IconButton icon={Pencil} label={`Edit #${c.name}`} onClick={() => setEditing(c)} />
                <Menu
                  label={`More actions for #${c.name}`}
                  icon={MoreHorizontal}
                  items={[
                    ...(c.is_default
                      ? []
                      : [
                          {
                            label: "Make default",
                            icon: Star,
                            onSelect: () =>
                              makeDefault.mutate(c.id, {
                                onSuccess: () => toast(`#${c.name} is now the default channel.`),
                                onError: (e) => toast(errorMessage(e), "bad"),
                              }),
                          },
                        ]),
                    { label: "Delete", icon: Trash2, danger: true, onSelect: () => setDeleting(c) },
                  ]}
                />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <Empty icon={Hash} title="No channels yet">
          Add a Discord channel so the bot has somewhere to post.
        </Empty>
      )}
      <Button icon={Plus} onClick={() => setAdding(true)}>
        Add channel
      </Button>
      <div style={{ marginTop: 14 }}>
        <WebhookHowTo />
      </div>
      <ChannelDialog open={adding || editing !== null} channel={editing} isFirst={list.length === 0} onClose={() => (setAdding(false), setEditing(null))} />
      <DeleteChannelDialog key={deleting?.id ?? "none"} channel={deleting} channels={list} onClose={() => setDeleting(null)} />
    </div>
  );
}
