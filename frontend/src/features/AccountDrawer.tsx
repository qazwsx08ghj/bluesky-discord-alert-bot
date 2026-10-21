import { createContext, useCallback, useContext, useEffect, useId, useMemo, useState, type ReactNode } from "react";
import { Check, Info, X } from "lucide-react";
import { ApiError, OfflineError, errorMessage } from "../api/client";
import { useAddAccount, useAddChannel, useChannels, useCheckNow, useProfileLookup, useUpdateAccount } from "../api/queries";
import type { Account, Channel, ProfileLookup } from "../api/types";
import { Avatar, Button, Dialog, FieldError, FieldWarn, ICON, ICON_LG, IconButton, useToast } from "../components/ui";
import { displayName } from "../lib/people";
import { plural } from "../lib/time";

type DrawerState = { mode: "add" } | { mode: "edit"; account: Account } | null;

interface DrawerApi {
  openAdd: () => void;
  openEdit: (account: Account) => void;
  /** The account added last, highlighted briefly in the table. */
  freshId: number | null;
}

const DrawerContext = createContext<DrawerApi>({ openAdd: () => undefined, openEdit: () => undefined, freshId: null });
export const useAccountDrawer = () => useContext(DrawerContext);

export function AccountDrawerProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<DrawerState>(null);
  const [freshId, setFreshId] = useState<number | null>(null);
  const toast = useToast();
  const checkNow = useCheckNow();

  const firstCheck = useCallback(
    (account: Account) => {
      setFreshId(account.id);
      window.setTimeout(() => setFreshId((id) => (id === account.id ? null : id)), 4000);
      if (!account.is_active) {
        toast(`Added @${account.handle}. It's paused, so it won't be checked until you resume it.`);
        return;
      }
      toast(`Watching @${account.handle}. Running the first check…`);
      checkNow.mutate(account.id, {
        onSuccess: (result) => {
          if (result.error) {
            toast(
              result.problem?.category === "bluesky_not_configured"
                ? `Added @${account.handle}. Its first check runs once the Bluesky login is set up.`
                : `Added @${account.handle}, but the first check didn't work: ${result.problem?.title ?? result.error}`,
              result.problem?.category === "bluesky_not_configured" ? "ok" : "bad",
            );
            return;
          }
          const noted = result.first_check?.seeded_posts;
          toast(
            result.was_first_poll
              ? `First check done for ${displayName(account)}. ${noted != null ? `Noted ${plural(noted, "existing post")}; none were sent. ` : ""}New posts will be announced.`
              : (result.check?.message ?? `Checked ${displayName(account)}.`),
          );
        },
        onError: (error) => {
          // Already being checked (the scheduled check got there first) is fine.
          if (error instanceof ApiError && error.code === "already_checking") return;
          toast(`Added @${account.handle}. The first check will run with the next scheduled check.`);
        },
      });
    },
    [checkNow, toast],
  );

  const api = useMemo<DrawerApi>(
    () => ({ openAdd: () => setState({ mode: "add" }), openEdit: (account) => setState({ mode: "edit", account }), freshId }),
    [freshId],
  );

  return (
    <DrawerContext.Provider value={api}>
      {children}
      <AccountDrawer
        state={state}
        onClose={() => setState(null)}
        onAdded={(account) => {
          setState(null);
          firstCheck(account);
        }}
      />
    </DrawerContext.Provider>
  );
}

function useDebounced(value: string, ms: number) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return debounced;
}

function cleanHandle(raw: string) {
  return raw.trim().replace(/^@/, "");
}

/** Whether the lookup answer allows adding, and what to say about it. */
function lookupVerdict(lookup: ProfileLookup | undefined, error: unknown): { canAdd: boolean; error?: string; warning?: string; suggestion?: string } {
  if (error instanceof OfflineError) return { canAdd: false, error: error.message };
  if (error) return { canAdd: true, warning: "Couldn't look this account up right now. You can still add it." };
  if (!lookup) return { canAdd: false };
  switch (lookup.result) {
    case "found":
      return lookup.already_watched ? { canAdd: false, error: lookup.message } : { canAdd: true };
    case "unavailable":
      return { canAdd: true, warning: lookup.message };
    case "rate_limited":
    case "error":
      return { canAdd: true, warning: `${lookup.message} You can still add it.` };
    case "invalid":
      return { canAdd: false, error: lookup.message, suggestion: lookup.suggestion ?? undefined };
    default:
      return { canAdd: false, error: lookup.message };
  }
}

function AccountDrawer({ state, onClose, onAdded }: { state: DrawerState; onClose: () => void; onAdded: (account: Account) => void }) {
  const id = useId();
  return (
    <Dialog open={state !== null} onClose={onClose} labelledBy={`${id}-t`} className="drawer">
      {state ? <DrawerBody key={state.mode === "edit" ? state.account.id : "add"} state={state} titleId={`${id}-t`} onClose={onClose} onAdded={onAdded} /> : null}
    </Dialog>
  );
}

function DrawerBody({
  state,
  titleId,
  onClose,
  onAdded,
}: {
  state: NonNullable<DrawerState>;
  titleId: string;
  onClose: () => void;
  onAdded: (account: Account) => void;
}) {
  const editing = state.mode === "edit" ? state.account : null;
  const toast = useToast();
  const channels = useChannels();
  const addAccount = useAddAccount();
  const updateAccount = useUpdateAccount();
  const addChannel = useAddChannel();

  const [handle, setHandle] = useState(editing?.handle ?? "");
  const [name, setName] = useState(editing?.display_name ?? "");
  const [nameTouched, setNameTouched] = useState(Boolean(editing));
  const [channel, setChannel] = useState<string>(editing?.channel_id ? String(editing.channel_id) : "default");
  const [newChannel, setNewChannel] = useState({ name: "", url: "" });
  const [announce, setAnnounce] = useState({
    posts: editing?.announce_posts ?? true,
    reposts: editing?.announce_reposts ?? true,
    replies: editing?.announce_replies ?? false,
  });
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState("");

  const query = useDebounced(cleanHandle(handle), 350);
  const typing = cleanHandle(handle) !== query;
  const lookup = useProfileLookup(editing ? "" : query);
  const verdict = editing ? { canAdd: true } : lookupVerdict(lookup.data, lookup.error);
  const profile = lookup.data?.result === "found" ? lookup.data.profile : null;

  useEffect(() => {
    if (profile && !nameTouched) setName(profile.display_name || "");
  }, [profile, nameTouched]);

  const all = channels.data ?? [];
  const defaultChannel = all.find((c) => c.is_default);
  const choices: Channel[] = all.filter((c) => !c.is_default || c.id === editing?.channel_id);
  const busy = addAccount.isPending || updateAccount.isPending || addChannel.isPending;
  const lookupSettled = Boolean(editing) || (!typing && !lookup.isFetching && (lookup.data !== undefined || lookup.error !== null));
  const canSubmit = !busy && cleanHandle(handle).length > 0 && lookupSettled && verdict.canAdd;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    setFieldErrors({});
    setFormError("");
    try {
      let channelId: number | null = channel === "default" ? null : Number(channel);
      if (channel === "new") {
        const created = await addChannel.mutateAsync({ name: newChannel.name.trim(), url: newChannel.url.trim() });
        channelId = created.id;
        setChannel(String(created.id));
      }
      const body = {
        display_name: name.trim(),
        channel_id: channelId,
        announce_posts: announce.posts,
        announce_reposts: announce.reposts,
        announce_replies: announce.replies,
      };
      if (editing) {
        await updateAccount.mutateAsync({ id: editing.id, ...body });
        toast(`Saved the changes to ${name.trim() || "@" + editing.handle}.`);
        onClose();
      } else {
        const watchAs = profile?.watch_as || lookup.data?.handle || cleanHandle(handle);
        const created = await addAccount.mutateAsync({ handle: watchAs, is_active: true, ...body });
        onAdded(created);
      }
    } catch (error) {
      if (error instanceof ApiError && Object.keys(error.fields).length) {
        const fields = Object.fromEntries(Object.entries(error.fields).map(([k, v]) => [k, v.join(" ")]));
        // A failed new channel reports name/url; show them on the channel fields.
        setFieldErrors(fields);
        if (!fields.handle && !fields.display_name && !fields.name && !fields.url && !fields.channel_id) setFormError(error.message);
      } else {
        setFormError(errorMessage(error));
      }
    }
  }

  const handleId = useId();
  const looking = !editing && cleanHandle(handle) && (typing || lookup.isFetching);
  return (
    <>
      <div className="drawer-h">
        <h2 id={titleId}>{editing ? `Edit ${displayName(editing)}` : "Add an account to watch"}</h2>
        <IconButton icon={X} label="Close" onClick={onClose} />
      </div>
      <form className="drawer-b" id={`${handleId}-form`} onSubmit={submit} noValidate>
        <div className="field">
          <label htmlFor={handleId}>Bluesky handle</label>
          <input
            id={handleId}
            className="input"
            value={handle}
            readOnly={Boolean(editing)}
            data-autofocus={editing ? undefined : true}
            autoComplete="off"
            spellCheck={false}
            placeholder="someone.bsky.social"
            aria-invalid={Boolean(verdict.error || fieldErrors.handle) || undefined}
            aria-describedby={`${handleId}-help ${handleId}-look`}
            onChange={(e) => setHandle(e.target.value)}
          />
          <span className="help" id={`${handleId}-help`}>
            {editing
              ? "To watch a different account, add it as a new one."
              : "Their handle is under their name on their Bluesky profile. You can also paste a link to the profile."}
          </span>
          <div id={`${handleId}-look`} aria-live="polite">
            {editing ? (
              <ProfileCard name={displayName(editing)} handle={editing.handle} />
            ) : looking ? (
              <div className="profile">
                <span className="spin" style={{ color: "var(--text-3)" }} aria-hidden="true" />
                <span>Looking up @{cleanHandle(handle)}…</span>
              </div>
            ) : profile && !lookup.data?.already_watched ? (
              <ProfileCard name={profile.display_name || "@" + profile.handle} handle={profile.handle} avatar={profile.avatar_url} found />
            ) : verdict.error ? (
              <FieldError>
                {verdict.error}{" "}
                {verdict.suggestion ? (
                  <button type="button" className="link-btn" onClick={() => setHandle(verdict.suggestion!)}>
                    Use {verdict.suggestion}
                  </button>
                ) : null}
              </FieldError>
            ) : verdict.warning ? (
              <FieldWarn>{verdict.warning}</FieldWarn>
            ) : null}
            <FieldError>{fieldErrors.handle}</FieldError>
          </div>
        </div>

        <div className="field">
          <label htmlFor={`${handleId}-name`}>Name shown in Discord</label>
          <input
            id={`${handleId}-name`}
            data-autofocus={editing ? true : undefined}
            className="input"
            value={name}
            maxLength={255}
            placeholder={editing ? "" : "Filled in from their profile"}
            onChange={(e) => {
              setName(e.target.value);
              setNameTouched(true);
            }}
          />
          <FieldError>{fieldErrors.display_name}</FieldError>
        </div>

        <div className="field">
          <label htmlFor={`${handleId}-chan`}>Post to channel</label>
          <select id={`${handleId}-chan`} className="select" value={channel} onChange={(e) => setChannel(e.target.value)}>
            <option value="default">{defaultChannel ? `Default channel (#${defaultChannel.name})` : "Default channel (none set up yet)"}</option>
            {choices.map((c) => (
              <option key={c.id} value={c.id}>
                #{c.name}
              </option>
            ))}
            <option value="new">Add a new channel…</option>
          </select>
          <span className="help">
            {channel === "default" ? "Follows the default channel, also if you change the default later." : "Only this account posts here, whatever the default is."}
          </span>
          <FieldError>{fieldErrors.channel_id}</FieldError>
        </div>
        {channel === "new" ? (
          <div className="subform">
            <div className="field">
              <label htmlFor={`${handleId}-cn`}>Channel name</label>
              <input id={`${handleId}-cn`} className="input" placeholder="art-drops" value={newChannel.name} aria-invalid={Boolean(fieldErrors.name) || undefined} onChange={(e) => setNewChannel({ ...newChannel, name: e.target.value })} />
              <span className="help">Just a label for you, usually the Discord channel's name.</span>
              <FieldError>{fieldErrors.name}</FieldError>
            </div>
            <div className="field">
              <label htmlFor={`${handleId}-cu`}>Webhook URL</label>
              <input id={`${handleId}-cu`} className="input mono" placeholder="https://discord.com/api/webhooks/…" autoComplete="off" spellCheck={false} value={newChannel.url} aria-invalid={Boolean(fieldErrors.url) || undefined} onChange={(e) => setNewChannel({ ...newChannel, url: e.target.value })} />
              <FieldError>{fieldErrors.url}</FieldError>
            </div>
          </div>
        ) : null}

        <fieldset className="field opts">
          <legend>What to announce</legend>
          <label className="opt">
            <input type="checkbox" checked={announce.posts} onChange={(e) => setAnnounce({ ...announce, posts: e.target.checked })} />
            <span>
              <b>Original posts</b>
              <small>Things they write themselves</small>
            </span>
          </label>
          <label className="opt">
            <input type="checkbox" checked={announce.reposts} onChange={(e) => setAnnounce({ ...announce, reposts: e.target.checked })} />
            <span>
              <b>Reposts</b>
              <small>Other people's posts they share</small>
            </span>
          </label>
          <label className="opt">
            <input type="checkbox" checked={announce.replies} onChange={(e) => setAnnounce({ ...announce, replies: e.target.checked })} />
            <span>
              <b>Replies</b>
              <small>Their replies in conversations. Can be noisy.</small>
            </span>
          </label>
          <span className="help">Changes only affect new posts. Older posts are never sent.</span>
        </fieldset>

        {editing ? null : (
          <div className="note">
            <Info {...ICON} />
            <div>The first check runs straight away. It only notes their existing posts, so your channel isn't flooded. New posts after that are announced.</div>
          </div>
        )}
        <div style={{ marginTop: 12 }}>
          <FieldError>{formError}</FieldError>
        </div>
      </form>
      <div className="drawer-f">
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button variant="primary" type="submit" form={`${handleId}-form`} disabled={!canSubmit} busy={busy} busyLabel={editing ? "Saving…" : "Adding…"}>
          {editing ? "Save changes" : "Add account"}
        </Button>
      </div>
    </>
  );
}

function ProfileCard({ name, handle, avatar, found }: { name: string; handle: string; avatar?: string | null; found?: boolean }) {
  return (
    <div className="profile">
      <Avatar name={name} handle={handle} src={avatar} size={44} />
      <div>
        <b>{name}</b>
        <span>@{handle}</span>
      </div>
      {found ? (
        <span className="ok-ic" title="Found on Bluesky">
          <Check {...ICON_LG} />
          <span className="sr">Found on Bluesky</span>
        </span>
      ) : null}
    </div>
  );
}
