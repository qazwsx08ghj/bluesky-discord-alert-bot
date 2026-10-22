import { useId, type ReactNode } from "react";
import { Plus, X } from "lucide-react";
import type { Account, Channel, DestinationInput } from "../api/types";
import { FieldError, ICON, IconButton } from "../components/ui";
import { HOW_TO_COPY_ROLE_ID, checkRoleId } from "../lib/roles";

/** The bot's limit (AccountDestination.MAX_PER_ACCOUNT). */
export const MAX_DESTINATIONS = 10;

/** One row of the form. `channel`: "default", a channel id, or "new" (created on save). */
export type DestinationRow = { key: number; channel: string; role: string; touched: boolean };

let nextKey = 1;
const row = (channel: string, role = ""): DestinationRow => ({ key: nextKey++, channel, role, touched: false });

export function initialRows(account: Account | null): DestinationRow[] {
  if (!account || account.destinations.length === 0) return [row("default")];
  return account.destinations.map((d) => row(d.channel_id === null ? "default" : String(d.channel_id), d.role_id));
}

/** What to save, once a new channel (if any) has its id. Roles must already be valid. */
export function toInput(rows: DestinationRow[], newChannelId: number | null): DestinationInput[] {
  return rows.map((r) => ({
    channel_id: r.channel === "default" ? null : r.channel === "new" ? newChannelId : Number(r.channel),
    role_id: checkRoleId(r.role).value,
  }));
}

export function rolesAreValid(rows: DestinationRow[]): boolean {
  return rows.every((r) => !checkRoleId(r.role).error);
}

/** Where an account's notifications go: one row per channel, each with an optional role to mention. */
export function DestinationsField({
  rows,
  onChange,
  channels,
  errors,
  showAllErrors,
  children,
}: {
  rows: DestinationRow[];
  onChange: (rows: DestinationRow[]) => void;
  channels: Channel[];
  errors: Record<string, string>;
  /** After a save attempt: show every invalid Role ID, also in fields not visited yet. */
  showAllErrors: boolean;
  /** The form for a new channel, shown under the list while a row is "Add a new channel…". */
  children?: ReactNode;
}) {
  const id = useId();
  const defaultChannel = channels.find((c) => c.is_default);
  const taken = (except: number) => new Set(rows.filter((r) => r.key !== except).map((r) => r.channel));
  const update = (key: number, change: Partial<DestinationRow>) => onChange(rows.map((r) => (r.key === key ? { ...r, ...change } : r)));
  const usedNow = taken(-1);
  const nextChoice = ["default", ...channels.filter((c) => !c.is_default).map((c) => String(c.id)), "new"].find((choice) => !usedNow.has(choice));

  return (
    <>
      <fieldset className="field dests" aria-describedby={`${id}-help`}>
        <legend>Discord channels</legend>
        <div className="dest-head" aria-hidden="true">
          <span>Channel</span>
          <span>Role to mention (optional)</span>
        </div>
        <ul className="dest-list">
          {rows.map((r, i) => {
            const n = rows.length > 1 ? ` ${i + 1}` : "";
            const used = taken(r.key);
            const role = checkRoleId(r.role);
            const roleError = errors[`destinations.${i}.role_id`] || ((r.touched || showAllErrors) && role.error) || "";
            const channelError = errors[`destinations.${i}.channel_id`] || "";
            return (
              <li key={r.key} className="dest-row">
                <select className="select" aria-label={`Channel${n}`} value={r.channel} aria-invalid={Boolean(channelError) || undefined} onChange={(e) => update(r.key, { channel: e.target.value })}>
                  {!used.has("default") ? <option value="default">{defaultChannel ? `Default channel (#${defaultChannel.name})` : "Default channel (none set up yet)"}</option> : null}
                  {channels
                    .filter((c) => (!c.is_default || String(c.id) === r.channel) && !used.has(String(c.id)))
                    .map((c) => (
                      <option key={c.id} value={c.id}>
                        #{c.name}
                      </option>
                    ))}
                  {!used.has("new") ? <option value="new">Add a new channel…</option> : null}
                </select>
                <input
                  className="input mono"
                  aria-label={`Role to mention${n ? ` in channel${n}` : ""} (optional)`}
                  inputMode="numeric"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="Role ID (optional)"
                  value={r.role}
                  aria-invalid={Boolean(roleError) || undefined}
                  onChange={(e) => update(r.key, { role: e.target.value })}
                  onBlur={() => update(r.key, { touched: true })}
                />
                {rows.length > 1 ? <IconButton icon={X} label={`Remove channel${n}`} onClick={() => onChange(rows.filter((x) => x.key !== r.key))} /> : <span aria-hidden="true" />}
                {channelError || roleError ? (
                  <div className="dest-errs">
                    <FieldError>{channelError}</FieldError>
                    <FieldError>{roleError}</FieldError>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
        {rows.length >= MAX_DESTINATIONS ? (
          <span className="help">An account can send to at most {MAX_DESTINATIONS} channels.</span>
        ) : nextChoice ? (
          <button type="button" className="link-btn dest-add" onClick={() => onChange([...rows, row(nextChoice)])}>
            <Plus {...ICON} />
            Add another channel
          </button>
        ) : null}
        {children}
        <span className="help" id={`${id}-help`}>
          Leave the role empty to send without mentioning anyone. The default channel follows your default, also if you change it later.
        </span>
        <FieldError>{errors.destinations}</FieldError>
      </fieldset>
      <details className="howto">
        <summary>How do I find a Role ID?</summary>
        <ol>
          {HOW_TO_COPY_ROLE_ID.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      </details>
    </>
  );
}
