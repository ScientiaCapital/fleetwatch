"""One heartbeat: read the fleet, work out what changed, say it once. No LLM, no writes."""

import logging
from datetime import UTC, datetime, timedelta

from fleetwatch.agents.readiness.rules import check as readiness_check
from fleetwatch.agents.room_state.rules import room_state
from fleetwatch.agents.scanner.rules import scan
from fleetwatch.epiphan.mcp import EpiphanClient
from fleetwatch.epiphan.parse import apply_events, apply_recorder_status, apply_system_status, parse_devices
from fleetwatch.model import Fleet, Priority, RoomState
from fleetwatch.notify.digest import render_digest, render_readiness
from fleetwatch.notify.slack import Notifier
from fleetwatch.policy import Policy
from fleetwatch.redact import redact
from fleetwatch.state import State

log = logging.getLogger(__name__)


async def snapshot(client: EpiphanClient, now: datetime) -> Fleet:
    fleet = parse_devices(await client.call("get_devices_in_my_team"), now)
    online = [d.id for d in fleet.devices.values() if d.online]
    if online:
        for tool, apply in (
            ("get_recorder_status_for_devices", apply_recorder_status),
            ("get_system_status_for_devices", apply_system_status),
        ):
            try:
                apply(fleet, await client.call(tool, {"device_ids": online}))
            except Exception as e:  # noqa: BLE001  (one failed read shouldn't lose the heartbeat)
                log.warning("%s failed: %s", tool, redact(str(e)))
    try:
        until = (now + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        apply_events(fleet, await client.call("get_current_or_next_cms_events_for_devices", {"until": until}))
    except Exception as e:  # noqa: BLE001
        log.warning("event lookup failed: %s", redact(str(e)))
    return fleet


def health(state: State, heartbeat_seconds: int, now: datetime | None = None) -> tuple[bool, str]:
    """Healthy when a heartbeat read the fleet within the last three intervals. Used by `status --check`."""
    now = now or datetime.now(UTC)
    last = state.last_snapshot()
    every = f"every {max(1, round(heartbeat_seconds / 60))} min"
    if last is None:
        return False, f"No heartbeat yet (expected {every})"
    ago = int((now - last).total_seconds() // 60)
    if now - last <= timedelta(seconds=3 * heartbeat_seconds):
        return True, f"Last heartbeat {ago} min ago"
    return False, f"Last heartbeat {ago} min ago; expected {every}"


async def tick(
    client: EpiphanClient,
    state: State,
    policy: Policy,
    notifier: Notifier,
    *,
    first_run: bool = False,
    now: datetime | None = None,
) -> str | None:
    now = now or datetime.now(UTC)
    fleet = await snapshot(client, now)
    state.snapshot(now, len(fleet.devices), sum(d.online for d in fleet.devices.values()))
    state.record_devices(fleet, now)

    findings = scan(fleet, policy)
    new, reminders, resolved = state.reconcile(findings, now, timedelta(minutes=policy.remind_after_minutes))
    quiet = policy.in_quiet_hours(now.astimezone().time())
    if quiet:  # only Fix first gets through at night; the rest waits (never sent, so it posts later)
        new = [f for f in new if f.priority is Priority.FIX_FIRST]
        reminders, resolved = [], []
    text = render_digest(new, reminders, resolved, first_run=first_run and not quiet)
    if text and notifier.post(text):
        state.mark_sent(new + reminders, now)
        state.audit(
            "digest",
            {
                "new": [f.key for f in new],
                "reminders": [f.key for f in reminders],
                "resolved": [f.key for f in resolved],
            },
            now,
        )

    lead = timedelta(minutes=policy.lead_minutes)
    for dev_id, event in fleet.events.items():
        dev = fleet.devices.get(dev_id)
        if dev is None or state.readiness_posted(event.key):
            continue
        if room_state(dev, event, now, lead) is RoomState.PRE_CLASS or (
            not dev.online and now < event.start <= now + lead
        ):
            r = readiness_check(dev, event, policy)
            if notifier.post(render_readiness(r)):
                state.record_readiness(r, now)
                state.audit("readiness", {"event": event.key, "verdict": r.verdict}, now)
    return text
