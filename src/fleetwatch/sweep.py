"""Nightly sweep (#21): once a day, a calm summary of who is offline and who is behind on firmware, compared with
the previous sweep, and kept as history. It never opens findings; the heartbeat stays the only source of alerts.

It runs at `sweep_at` (local time) or at the first heartbeat after it. If that falls in quiet hours, the summary is
saved and posted at the first heartbeat after they end.
"""

from datetime import datetime, time, timedelta

from fleetwatch.agents.scanner.rules import _version, firmware_family, in_scope, newest_firmware
from fleetwatch.epiphan.mcp import EpiphanClient
from fleetwatch.heartbeat import snapshot
from fleetwatch.model import Fleet, SweepResult
from fleetwatch.notify.slack import Notifier
from fleetwatch.policy import Policy
from fleetwatch.state import State

KEEP_SNAPSHOTS = timedelta(days=90)
MAX_NAMES = 10


def due(now: datetime, sweep_at: time | None, last: datetime | None) -> bool:
    """True once per local day, from `sweep_at` on. Pass local times."""
    if sweep_at is None or now.time() < sweep_at:
        return False
    return last is None or last.date() < now.date()


def summarize(fleet: Fleet, policy: Policy, now: datetime, previous: SweepResult | None) -> SweepResult:
    devices = sorted((d for d in fleet.devices.values() if in_scope(d, policy)), key=lambda d: d.name.casefold())
    newest = newest_firmware(fleet)
    offline = tuple(d.name for d in devices if not d.online)
    behind = tuple(
        (d.name, d.firmware, newest[fam])
        for d in devices
        if d.firmware and (fam := firmware_family(d.model)) in newest and _version(d.firmware) < _version(newest[fam])
    )
    newly_offline: tuple[str, ...] = ()
    back_online: tuple[str, ...] = ()
    if previous is not None:
        before = set(previous.offline)
        newly_offline = tuple(n for n in offline if n not in before)
        online_now = {d.name for d in devices if d.online}
        back_online = tuple(sorted((n for n in before if n in online_now), key=str.casefold))
    return SweepResult(
        at=now,
        devices=len(devices),
        online=sum(d.online for d in devices),
        offline=offline,
        behind=behind,
        newly_offline=newly_offline,
        back_online=back_online,
    )


def _names(names: tuple[str, ...]) -> str:
    shown = ", ".join(names[:MAX_NAMES])
    return shown + (f", and {len(names) - MAX_NAMES} more" if len(names) > MAX_NAMES else "")


def render_sweep(s: SweepResult) -> str:
    lines = [f"*Nightly sweep* · {s.devices} devices, {s.online} online"]
    if s.offline:
        lines.append(f"• Offline ({len(s.offline)}): {_names(s.offline)}")
    if s.behind:
        shown = ", ".join(f"{n} ({fw}; newest {new})" for n, fw, new in s.behind[:MAX_NAMES])
        more = f", and {len(s.behind) - MAX_NAMES} more" if len(s.behind) > MAX_NAMES else ""
        lines.append(f"• Behind on firmware ({len(s.behind)}): {shown}{more}")
    changes = []
    if s.newly_offline:
        changes.append(f"newly offline: {_names(s.newly_offline)}")
    if s.back_online:
        changes.append(f"back online: {_names(s.back_online)}")
    if changes:
        lines.append(f"• Since the last sweep, {'; '.join(changes)}")
    if not s.offline and not s.behind:
        lines.append("Everything is online and on the same firmware.")
    return "\n".join(lines)


async def run_sweep(client: EpiphanClient, state: State, policy: Policy, notifier: Notifier, now: datetime) -> bool:
    """Read the fleet, save the sweep, post it unless it's quiet hours. Returns whether it was posted."""
    fleet = await snapshot(client, now)
    result = summarize(fleet, policy, now, state.last_sweep())
    sweep_id = state.record_sweep(result)
    state.audit(
        "sweep",
        {
            "devices": result.devices,
            "online": result.online,
            "offline": len(result.offline),
            "behind": len(result.behind),
        },
        now,
    )
    state.prune_snapshots(now - KEEP_SNAPSHOTS)
    return _post(state, policy, notifier, now, result, sweep_id)


def post_pending(state: State, policy: Policy, notifier: Notifier, now: datetime) -> bool:
    """Post a sweep that was saved during quiet hours, once they're over."""
    pending = state.unposted_sweep()
    return pending is not None and _post(state, policy, notifier, now, pending, pending.id)


def _post(
    state: State, policy: Policy, notifier: Notifier, now: datetime, s: SweepResult, sweep_id: int | None
) -> bool:
    if policy.in_quiet_hours(now.astimezone().time()) or sweep_id is None:
        return False
    if notifier.post(render_sweep(s)):
        state.mark_sweep_posted(sweep_id, now)
        return True
    return False


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def render_history(state: State, since: datetime, now: datetime) -> str:
    """One line per local day: fleet size, online range, posts, and that day's sweep."""
    days: dict[str, dict] = {}

    def day(at: datetime) -> dict:
        return days.setdefault(
            at.astimezone().date().isoformat(), {"sizes": [], "online": [], "kinds": {}, "sweep": None}
        )

    for at, n, online in state.snapshots_since(since):
        d = day(at)
        d["sizes"].append(n)
        d["online"].append(online)
    for at, kind, _ in state.audit_since(since):
        if kind in ("digest", "readiness"):
            d = day(at)
            d["kinds"][kind] = d["kinds"].get(kind, 0) + 1
    for s in state.sweeps_since(since):
        day(s.at)["sweep"] = s
    if not days:
        return "No history yet. It starts with the first heartbeat."

    lines = [f"History since {since.astimezone():%Y-%m-%d} (now {now.astimezone():%Y-%m-%d %H:%M})"]
    for date in sorted(days):
        d = days[date]
        parts = []
        if d["sizes"]:
            lo, hi = min(d["online"]), max(d["online"])
            span = str(lo) if lo == hi else f"{lo}-{hi}"
            parts.append(f"{max(d['sizes'])} devices, {span} online")
        if d["kinds"].get("digest"):
            parts.append(_plural(d["kinds"]["digest"], "digest"))
        if d["kinds"].get("readiness"):
            parts.append(_plural(d["kinds"]["readiness"], "readiness check"))
        if d["sweep"]:
            s = d["sweep"]
            parts.append(f"sweep: {len(s.offline)} offline, {len(s.behind)} behind on firmware")
        lines.append(f"{date}  " + " · ".join(parts))
    return "\n".join(lines)
