"""Nightly sweep (#21): once a day, a calm summary of who is offline and who is behind on firmware, compared with
the previous sweep, and kept as history. It never opens findings; the heartbeat stays the only source of alerts.

It runs at `sweep_at` (local time) or at the first heartbeat after it. If that falls in quiet hours, the summary is
saved and posted at the first heartbeat after they end.
"""

from datetime import datetime, time, timedelta

from fleetwatch.agents.scanner.rules import _version, firmware_family, in_scope, newest_firmware
from fleetwatch.epiphan.mcp import EpiphanClient
from fleetwatch.heartbeat import snapshot
from fleetwatch.i18n import t
from fleetwatch.model import Fleet, SweepResult
from fleetwatch.notify import Notifier
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


def _names(names: tuple[str, ...], lang: str = "en") -> str:
    shown = ", ".join(names[:MAX_NAMES])
    return shown + (t(lang, "sweep.more", n=len(names) - MAX_NAMES) if len(names) > MAX_NAMES else "")


def render_sweep(s: SweepResult, lang: str = "en") -> str:
    lines = [t(lang, "sweep.head", devices=s.devices, online=s.online)]
    if s.offline:
        lines.append(t(lang, "sweep.offline", n=len(s.offline), names=_names(s.offline, lang)))
    if s.behind:
        shown = ", ".join(
            t(lang, "sweep.behind_item", name=n, firmware=fw, newest=new) for n, fw, new in s.behind[:MAX_NAMES]
        )
        more = t(lang, "sweep.more", n=len(s.behind) - MAX_NAMES) if len(s.behind) > MAX_NAMES else ""
        lines.append(t(lang, "sweep.behind", n=len(s.behind), shown=shown, more=more))
    changes = []
    if s.newly_offline:
        changes.append(t(lang, "sweep.newly_offline", names=_names(s.newly_offline, lang)))
    if s.back_online:
        changes.append(t(lang, "sweep.back_online", names=_names(s.back_online, lang)))
    if changes:
        lines.append(t(lang, "sweep.changes", changes="; ".join(changes)))
    if not s.offline and not s.behind:
        lines.append(t(lang, "sweep.all_good"))
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
    if notifier.post(render_sweep(s, policy.language)):
        state.mark_sweep_posted(sweep_id, now)
        return True
    return False


def _count(lang: str, key: str, n: int) -> str:
    return t(lang, f"{key}.one") if n == 1 else t(lang, f"{key}.many", n=n)


def render_history(state: State, since: datetime, now: datetime, lang: str = "en") -> str:
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
        return t(lang, "history.none")

    lines = [t(lang, "history.head", since=f"{since.astimezone():%Y-%m-%d}", now=f"{now.astimezone():%Y-%m-%d %H:%M}")]
    for date in sorted(days):
        d = days[date]
        parts = []
        if d["sizes"]:
            lo, hi = min(d["online"]), max(d["online"])
            span = str(lo) if lo == hi else f"{lo}-{hi}"
            parts.append(t(lang, "history.fleet", size=max(d["sizes"]), span=span))
        if d["kinds"].get("digest"):
            parts.append(_count(lang, "history.digest", d["kinds"]["digest"]))
        if d["kinds"].get("readiness"):
            parts.append(_count(lang, "history.readiness", d["kinds"]["readiness"]))
        if d["sweep"]:
            s = d["sweep"]
            parts.append(t(lang, "history.sweep", offline=len(s.offline), behind=len(s.behind)))
        lines.append(f"{date}  " + " · ".join(parts))
    return "\n".join(lines)
