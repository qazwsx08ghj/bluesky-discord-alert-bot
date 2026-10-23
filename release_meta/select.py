"""Which release to install, if any. See docs/RELEASES.md, "Choosing a release".

decide(installed, channel, candidates, withdrawn, ...) -> Decision

Rules, in order (the first that applies decides):
1. The installed version must be a version (a development build can't be updated automatically).
2. The withdrawal list must not be older than the newest one this install accepted (revision
   went down: stale or replayed), and the same revision must have the same content (otherwise
   it was changed without a new revision, or altered).
3. Candidates are the releases of the channel that are newer than the installed version and not
   withdrawn. Stable channel: final versions only. Pre-release channel: everything.
4. If any candidate is invalid or inconsistent, nothing is installed. There is no fallback to an
   older release: that would let one bad release steer installs backwards. The fix is to repair
   or withdraw that release. A candidate whose manifest uses a format this updater doesn't know
   (a newer schema) blocks in the same way, with the advice to install a newer updater by hand.
5. No candidate: up to date (with a warning if the installed version is withdrawn).
6. The target is the newest candidate. Its min_updater must be met.
7. If the installed version is older than the target's min_from_version, or the target lists
   stepping stones newer than the installed version, the path goes through intermediate releases
   (each valid, not withdrawn, on the channel). Stepping stones are waypoints: never skipped,
   also not by a newer intermediate release. No path: nothing is installed.
A version older than or equal to the installed one is never chosen.
"""
from dataclasses import dataclass, field

from . import RELEASES_PAGE, UPDATER_PROTOCOL, MetadataError, semver
from . import withdrawn as withdrawn_rules
from .manifest import Manifest
from .withdrawn import Seen, Withdrawn

UPDATE, UP_TO_DATE, BLOCKED = "update", "up_to_date", "blocked"


@dataclass(frozen=True)
class Candidate:
    """One published release: its version (from the tag), and its manifest or why it is unusable."""
    version: semver.Version
    manifest: Manifest | None = None
    problem: str = ""
    needs_newer_updater: bool = False  # its manifest uses a format this updater doesn't know


@dataclass
class Decision:
    action: str                      # update | up_to_date | blocked
    reason: str                      # a short code, for tests and logs
    message: str                     # plain words for the user
    steps: list = field(default_factory=list)   # Manifests to install, in order (last = target)
    installed_withdrawn: object = None          # withdrawn.Entry if the installed version is withdrawn
    remember: Seen | None = None                # the withdrawal list to store as seen (None: store nothing)

    @property
    def target(self):
        return self.steps[-1] if self.steps else None


def _path(at, target: Manifest, usable: dict, memo=None):
    """The releases to install, in order, to get from version `at` to target; None if impossible.

    A stepping stone of a release, newer than `at`, must be installed before it (stones are
    waypoints, never skipped). Otherwise the target is installed directly if its
    min_from_version allows it, or through the newest intermediate release that leads there.
    """
    memo = {} if memo is None else memo
    key = (at, target.version)
    if key in memo:
        return memo[key]
    memo[key] = None  # guards against loops while this one is being worked out
    result = None
    stone = min((s for s in target.stepping_stones if s > at), default=None)
    if stone is not None:
        waypoint = usable.get(stone)
        first = _path(at, waypoint, usable, memo) if waypoint is not None else None
        rest = _path(stone, target, usable, memo) if first is not None else None
        result = first + rest if first is not None and rest is not None else None
    elif target.min_from_version <= at:
        result = [target]
    else:
        for middle in sorted((m for v, m in usable.items() if at < v < target.version),
                             key=lambda m: m.version, reverse=True):
            first = _path(at, middle, usable, memo)
            rest = _path(middle.version, target, usable, memo) if first is not None else None
            if first is not None and rest is not None:
                result = first + rest
                break
    memo[key] = result
    return result


def _blocked(reason, message, **kw):
    return Decision(BLOCKED, reason, message + " Nothing was changed.", **kw)


NEWER_UPDATER_ADVICE = (f"A newer updater is needed. Download the newest version from {RELEASES_PAGE} "
                        "and run update.bat in its folder.")


def decide(installed, channel: str, candidates, withdrawn: Withdrawn, seen: Seen | None = None,
           updater_protocol: int = UPDATER_PROTOCOL) -> Decision:
    try:
        current = semver.parse(installed)
    except MetadataError:
        return _blocked("installed_unknown",
                        f"The installed version ({str(installed)[:40]!r}) isn't a released version, so it can't be "
                        "updated automatically.")
    if channel not in ("stable", "prerelease"):
        return _blocked("channel_unknown", f"Unknown update channel {str(channel)[:20]!r}.")
    relation = withdrawn_rules.compare(seen, withdrawn)
    if relation == withdrawn_rules.OLDER:
        return _blocked("withdrawn_stale",
                        f"The list of withdrawn versions is older (revision {withdrawn.revision}) than one this "
                        f"computer has already accepted (revision {seen.revision}). It may be out of date or replayed.")
    if relation == withdrawn_rules.CONFLICT:
        return _blocked("withdrawn_conflict",
                        f"The list of withdrawn versions has the same revision ({withdrawn.revision}) as one this "
                        "computer has already accepted, but different contents. It was changed without a new "
                        "revision, or altered.")
    remember = withdrawn_rules.remember(seen, withdrawn)
    installed_withdrawn = withdrawn.get(current)

    on_channel = [c for c in candidates if channel == "prerelease" or not c.version.is_prerelease]
    versions = [c.version for c in candidates]
    if len(versions) != len(set(versions)):
        return _blocked("duplicate_release", "Two published releases have the same version.")
    newer = sorted((c for c in on_channel if c.version > current and c.version not in withdrawn),
                   key=lambda c: c.version)
    bad = [c for c in newer if c.manifest is None]
    unreadable = [c for c in bad if not c.needs_newer_updater]
    if unreadable:
        return _blocked("invalid_release",
                        f"The release information for {unreadable[-1].version} is invalid or inconsistent "
                        f"({unreadable[-1].problem[:200]}).", installed_withdrawn=installed_withdrawn,
                        remember=remember)
    if bad:
        return _blocked("updater_too_old",
                        f"{bad[-1].version} is described in a newer format than this updater can read safely. "
                        f"{NEWER_UPDATER_ADVICE}", installed_withdrawn=installed_withdrawn, remember=remember)
    if not newer:
        message = f"You have the newest version ({current})."
        if installed_withdrawn:
            message += (f" Note: {current} was withdrawn: {installed_withdrawn.reason}")
        return Decision(UP_TO_DATE, "up_to_date", message, installed_withdrawn=installed_withdrawn,
                        remember=remember)

    target = newer[-1].manifest
    steps = _path(current, target, {c.version: c.manifest for c in newer})
    if steps is None:
        missing = [s for s in target.stepping_stones if s > current and s not in {c.version for c in newer}]
        withdrawn_stones = [s for s in missing if s in withdrawn]
        if withdrawn_stones:
            why = f"it must be reached through {withdrawn_stones[0]}, which was withdrawn"
        elif missing:
            why = f"it must be reached through {missing[0]}, which isn't available"
        else:
            why = "a required intermediate version isn't available"
        return _blocked("no_path", f"{target.version} can't be reached from {current} by updating ({why}): "
                        "a manual update is needed.", installed_withdrawn=installed_withdrawn, remember=remember)

    too_new = next((m for m in steps if m.min_updater > updater_protocol), None)
    if too_new:
        return _blocked("updater_too_old",
                        f"{too_new.version} needs a newer updater (protocol {too_new.min_updater}; this one has "
                        f"{updater_protocol}). {NEWER_UPDATER_ADVICE}", installed_withdrawn=installed_withdrawn,
                        remember=remember)
    if any(m.version <= current for m in steps):  # a guard: the rules above never produce this
        return _blocked("downgrade", "The update would go to an older version.", remember=remember)
    path = " then ".join(str(m.version) for m in steps)
    return Decision(UPDATE, "update", f"{target.version} is available (you have {current}). Update path: {path}.",
                    steps=steps, installed_withdrawn=installed_withdrawn, remember=remember)
