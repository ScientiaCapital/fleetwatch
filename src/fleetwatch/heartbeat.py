"""One heartbeat: read the fleet, work out what changed, say it once. No LLM, no writes."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fleetwatch.agents.readiness.rules import check as readiness_check
from fleetwatch.agents.room_state.rules import room_state
from fleetwatch.agents.scanner.rules import scan
from fleetwatch.epiphan.mcp import EpiphanClient
from fleetwatch.epiphan.parse import (
    apply_events,
    apply_recorder_status,
    apply_system_status,
    device_items,
    parse_devices,
)
from fleetwatch.model import Fleet, Priority, RoomState
from fleetwatch.notify import Notifier
from fleetwatch.notify.digest import render_digest, render_readiness
from fleetwatch.policy import Policy
from fleetwatch.redact import redact
from fleetwatch.state import State

log = logging.getLogger(__name__)


class FailedRead(RuntimeError):
    """The device list couldn't be read. Never treat it as an empty fleet: that would mark everything fixed."""


async def snapshot(client: EpiphanClient, now: datetime) -> Fleet:
    """Read the fleet. Raises FailedRead when the device list is text, or a shape we don't know."""
    raw = await client.call("get_devices_in_my_team")
    if device_items(raw) is None:
        kind = "text instead of JSON" if isinstance(raw, str) else "a shape Fleetwatch doesn't know"
        raise FailedRead(f"get_devices_in_my_team returned {kind}")
    fleet = parse_devices(raw, now)
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
    on_fleet: Callable[[Fleet], None] | None = None,
) -> str | None:
    now = now or datetime.now(UTC)
    try:
        fleet = await snapshot(client, now)
        last = state.last_snapshot_count()
        if not fleet.devices and last:
            raise FailedRead(f"get_devices_in_my_team returned 0 devices; the last read had {last}")
    except FailedRead as e:
        # Nothing below runs: no diff, no "Back to normal", no post, and no snapshot that `status --check`
        # would count as a healthy heartbeat.
        log.warning("failed read, skipping this heartbeat: %s", e)
        raise
    if on_fleet is not None:  # lets /fleetwatch answer from this beat's fleet without reading it again
        on_fleet(fleet)
    state.snapshot(now, len(fleet.devices), sum(d.online for d in fleet.devices.values()))
    state.record_devices(fleet, now)

    findings = scan(fleet, policy)
    new, reminders, resolved = state.reconcile(findings, now, timedelta(minutes=policy.remind_after_minutes))
    quiet = policy.in_quiet_hours(now.astimezone().time())
    if quiet:  # only Fix first gets through at night; the rest waits (never sent, so it posts later)
        new = [f for f in new if f.priority is Priority.FIX_FIRST]
        reminders, resolved = [], []
    notes = state.notes_by_device({f.device_id for f in new + reminders})
    text = render_digest(new, reminders, resolved, first_run=first_run and not quiet, notes=notes)
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
