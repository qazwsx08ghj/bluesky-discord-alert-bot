"""A snapshot of the bot's data, and what putting a backup back would undo.

snapshot(path) reads a database copy (only reads; plain SQLite, so it works on any copy) and
records, per kind of data, a fingerprint of every row. compare(now, backup) then lists what
exists or changed in the data now but not in the backup: exactly what a restore of that
backup would lose.

What counts, and what doesn't:
- Things you set up: accounts (and their notification choices), Discord channels, where each
  account sends (and its role), the bot's settings (Bluesky login, check frequency).
  Secrets are part of the fingerprint only; they are never shown.
- History: posts the bot noticed, and notifications that were sent, failed or cancelled.
- Not counted as a loss: the bot's own bookkeeping that it redoes by itself (last check
  times, check locks, delivery retries in progress, a channel's last result). The activity
  log is reported, but it doesn't count as a loss on its own.

A restore that loses something from the first two groups needs RESTORE typed in restore.bat.
Posts the bot noticed after the backup are forgotten by a restore: the next check finds them
again and sends them, so notifications already sent can arrive a second time.
"""
import hashlib
import json
from dataclasses import dataclass, field

from botconfig.sqlite_files import open_read_only

# kind: (table, columns ignored: bookkeeping the bot redoes by itself)
SETUP_TABLES = {
    "accounts": ("bluesky_monitor_monitoredaccount", {
        "check_lock_token", "check_lock_until", "last_checked_at", "last_check_ok", "last_check_category",
        "last_success_at", "last_error_at", "last_error_category", "first_poll_done", "first_checked_at",
        "first_check_posts", "created_at"}),
    "channels": ("discord_alerts_discordwebhook", {
        "last_delivery_at", "last_delivery_ok", "last_delivery_error", "updated_at", "created_at", "guild_id"}),
    "destinations": ("bluesky_monitor_accountdestination", {"created_at"}),
    "settings": ("botconfig_botsettings", {"version", "updated_at", "created_at", "source", "imported_at"}),
}
FINISHED = ("sent", "failed", "cancelled")


def _fingerprint(row: dict) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()


def _tables(con) -> set:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _rows(con, table, ignored=()):
    cur = con.execute(f'SELECT * FROM "{table}"')  # table names come from SETUP_TABLES only
    names = [d[0] for d in cur.description]
    for values in cur:
        yield {k: v for k, v in zip(names, values) if k not in ignored}


def snapshot(path) -> dict:
    """Fingerprints of a database copy. Never use it on the live file (it is opened as unchanging)."""
    con = open_read_only(path)
    try:
        tables = _tables(con)
        snap = {"labels": {}}
        for kind, (table, ignored) in SETUP_TABLES.items():
            rows = {}
            if table in tables:
                for row in _rows(con, table, ignored):
                    rows[str(row["id"])] = _fingerprint(row)
                    label = row.get("handle") or row.get("name")
                    if label:
                        snap["labels"][f"{kind}:{row['id']}"] = label
            snap[kind] = rows
        posts, deliveries, sending = {}, {}, 0
        if "bluesky_monitor_seenpost" in tables:
            for account_id, uri, status in con.execute(
                    "SELECT account_id, post_uri, status FROM bluesky_monitor_seenpost"):
                posts[f"{account_id}|{uri}"] = status
        if "bluesky_monitor_postdelivery" in tables:
            for account_id, uri, channel_id, status in con.execute(
                    "SELECT p.account_id, p.post_uri, d.channel_id, d.status FROM bluesky_monitor_postdelivery d "
                    "JOIN bluesky_monitor_seenpost p ON p.id = d.post_id"):
                deliveries[f"{account_id}|{uri}|{channel_id}"] = status
                sending += status == "sending"
        snap["posts"], snap["deliveries"], snap["sending"] = posts, deliveries, sending
        snap["activity"] = sorted(r[0] for r in con.execute("SELECT id FROM activity_activityevent")) \
            if "activity_activityevent" in tables else []
        return snap
    finally:
        con.close()


@dataclass
class Impact:
    """What restoring `backup` would undo, compared with `now`."""
    added: dict = field(default_factory=dict)     # kind -> labels that exist now, not in the backup
    removed: dict = field(default_factory=dict)   # kind -> labels deleted since the backup (they'd come back)
    changed: dict = field(default_factory=dict)   # kind -> labels changed since the backup
    settings_changed: bool = False
    new_posts: int = 0           # posts noticed since the backup
    finished: dict = field(default_factory=dict)  # status -> deliveries that reached it since the backup
    activity: int = 0            # activity entries since the backup
    sending_now: int = 0         # deliveries in "sending" right now

    @property
    def loses_data(self) -> bool:
        return bool(any(self.added.values()) or any(self.removed.values()) or any(self.changed.values())
                    or self.settings_changed or self.new_posts or any(self.finished.values()))


def compare(now: dict, backup: dict) -> Impact:
    impact = Impact(sending_now=now.get("sending", 0))
    labels = {**backup.get("labels", {}), **now.get("labels", {})}
    for kind in SETUP_TABLES:
        a, b = now.get(kind, {}), backup.get(kind, {})
        if kind == "settings":
            impact.settings_changed = a != b
            continue
        name = lambda key: labels.get(f"{kind}:{key}", f"#{key}")  # noqa: E731
        impact.added[kind] = sorted(name(k) for k in a.keys() - b.keys())
        impact.removed[kind] = sorted(name(k) for k in b.keys() - a.keys())
        impact.changed[kind] = sorted(name(k) for k in a.keys() & b.keys() if a[k] != b[k])
    impact.new_posts = len(now["posts"].keys() - backup["posts"].keys())
    for key, status in now["deliveries"].items():
        if status in FINISHED and backup["deliveries"].get(key) != status:
            impact.finished[status] = impact.finished.get(status, 0) + 1
    impact.activity = len(set(now["activity"]) - set(backup["activity"]))
    return impact


def fingerprint(snap: dict) -> str:
    """A short code for what counts in the data (what you set up, posts, finished notifications).
    Bookkeeping and the activity log don't change it. update.bat compares it before and after
    starting a new version, to know whether anything that counts was written meanwhile."""
    gated = {kind: snap.get(kind, {}) for kind in SETUP_TABLES}
    gated["posts"] = sorted(snap.get("posts", {}))
    gated["finished"] = sorted(f"{k}={s}" for k, s in snap.get("deliveries", {}).items() if s in FINISHED)
    return hashlib.sha256(json.dumps(gated, sort_keys=True).encode()).hexdigest()[:20]


def confirmation_code(now: dict, impact: Impact) -> str:
    """A short code for "this impact, on the data as it is now".

    It changes when anything a restore would lose changes: what you set up, the posts noticed,
    or a notification that was sent, failed or was cancelled. Bookkeeping, retries in progress
    and the activity log don't change it. restore.bat passes the code of the report you
    confirmed to the restore, which recomputes it with the bot stopped (restore_db --expect).
    """
    gated = {kind: now.get(kind, {}) for kind in SETUP_TABLES}
    gated["posts"] = sorted(now.get("posts", {}))
    gated["finished"] = sorted(f"{k}={s}" for k, s in now.get("deliveries", {}).items() if s in FINISHED)
    gated["impact"] = describe(impact)
    gated["loses"] = impact.loses_data
    return hashlib.sha256(json.dumps(gated, sort_keys=True).encode()).hexdigest()[:20]


def _plural(n, word, plural=None):
    return f"{n} {word}" if n == 1 else f"{n} {plural or word + 's'}"


def _names(items, limit=5):
    shown = ", ".join(items[:limit])
    return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")


WHAT = {"accounts": ("account", "accounts"), "channels": ("Discord channel", "Discord channels"),
        "destinations": ("account channel choice", "account channel choices")}


def describe(impact: Impact) -> list[str]:
    """The impact in plain words, one line each. Empty when nothing would be lost."""
    lines = []
    for kind in ("accounts", "channels", "destinations"):
        one, many = WHAT[kind]
        show = kind != "destinations"
        for items, verb in ((impact.added.get(kind, []), "added since the backup would be removed"),
                            (impact.removed.get(kind, []), "deleted since the backup would come back"),
                            (impact.changed.get(kind, []), "changed since the backup would go back to how they were")):
            if items:
                names = f": {_names(items)}" if show else ""
                lines.append(f"{_plural(len(items), one, many)} {verb}{names}")
    if impact.settings_changed:
        lines.append("The bot's settings (Bluesky login or check frequency) changed since the backup "
                     "and would go back to how they were")
    if impact.new_posts:
        lines.append(f"{_plural(impact.new_posts, 'post')} the bot noticed since the backup would be forgotten")
    sent = impact.finished.get("sent", 0)
    if sent:
        lines.append(f"{_plural(sent, 'notification was', 'notifications were')} sent to Discord since the "
                     "backup. After the restore the bot may send these posts again (posts still among an "
                     "account's newest), so they can appear twice in Discord")
    other = impact.finished.get("failed", 0) + impact.finished.get("cancelled", 0)
    if other:
        lines.append(f"{_plural(other, 'notification')} that failed or was cancelled since the backup "
                     "would be tried again")
    return lines
