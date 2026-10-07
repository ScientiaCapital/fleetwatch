"""Turn Epiphan MCP tool results into the shared data model. Tolerant of missing keys: the agent must keep
running when a field is absent, and must never invent state it didn't read."""

from datetime import UTC, datetime
from typing import Any

from fleetwatch.model import Channel, Device, Event, Fleet, SystemStatus

STORAGE_WARNINGS = frozenset({"disk_space_error", "no_storage_detected"})


def _warning_ids(items: Any) -> list[str]:
    out = []
    for w in items or ():
        if isinstance(w, dict) and w.get("id"):
            out.append(str(w["id"]))
        elif isinstance(w, str):
            out.append(w)
    return out


def _is_recording(status: Any) -> bool:
    return isinstance(status, dict) and str(status.get("state", "")).lower() in {"recording", "started", "running"}


def parse_devices(raw: Any, taken_at: datetime | None = None) -> Fleet:
    """`get_devices_in_my_team` → Fleet with devices and channels (warnings, recording state)."""
    fleet = Fleet(taken_at=taken_at or datetime.now(UTC))
    items = raw.get("devices", raw) if isinstance(raw, dict) else raw
    for d in items or ():
        if not isinstance(d, dict) or not d.get("Id"):
            continue
        dev = Device(
            id=str(d["Id"]),
            name=str(d.get("Name") or d["Id"]),
            model=str(d.get("Model") or ""),
            group=str(d.get("GroupName") or ""),
            online=str(d.get("Status", "")).lower() == "online",
            firmware=str(d.get("Firmware") or ""),
            warnings=_warning_ids(d.get("Warnings")),
        )
        for c in d.get("Channels") or ():
            if not isinstance(c, dict):
                continue
            cid = str(c.get("channel_id") or c.get("id") or len(dev.channels) + 1)
            dev.channels[cid] = Channel(
                id=cid,
                name=str(c.get("name") or f"Channel {cid}"),
                warnings=_warning_ids(c.get("Warnings")),
                recording=_is_recording(c.get("recording_status")),
            )
        fleet.devices[dev.id] = dev
    return fleet


def apply_recorder_status(fleet: Fleet, raw: Any) -> None:
    """`get_recorder_status_for_devices` → per-channel recording flags (fresher than the device list)."""
    items = raw.get("devices", raw) if isinstance(raw, dict) else raw
    pairs = (
        items.items()
        if isinstance(items, dict)
        else ((d.get("device_id") or d.get("Id") or d.get("id"), d) for d in items or () if isinstance(d, dict))
    )
    for dev_id, d in pairs:
        dev = fleet.devices.get(str(dev_id))
        if dev is None or not isinstance(d, dict):
            continue
        channels = d.get("channels") or d.get("Channels") or {}
        chan_pairs = (
            channels.items()
            if isinstance(channels, dict)
            else ((c.get("channel_id") or c.get("id"), c) for c in channels if isinstance(c, dict))
        )
        for cid, c in chan_pairs:
            if not isinstance(c, dict):
                continue
            ch = dev.channels.setdefault(str(cid), Channel(id=str(cid), name=str(c.get("name") or f"Channel {cid}")))
            ch.recording = _is_recording(c.get("recording_status") or c.get("status") or c)


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _find(d: Any, *names: str) -> Any:
    """First value whose key contains any of `names` (case-insensitive), searching nested dicts."""
    if isinstance(d, dict):
        for k, v in d.items():
            if any(n in str(k).lower() for n in names) and not isinstance(v, (dict, list)):
                return v
        for v in d.values():
            found = _find(v, *names)
            if found is not None:
                return found
    return None


def _when(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        n = float(value)
        return datetime.fromtimestamp(n / 1000 if n > 1e11 else n, UTC)
    try:
        dt = datetime.fromisoformat(str(value))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def apply_system_status(fleet: Fleet, raw: Any) -> None:
    """`get_system_status_for_devices` → CPU load, temperature, uptime start."""
    items = raw.get("devices", raw) if isinstance(raw, dict) else raw
    pairs = (
        items.items()
        if isinstance(items, dict)
        else ((d.get("device_id") or d.get("Id") or d.get("id"), d) for d in items or () if isinstance(d, dict))
    )
    for dev_id, d in pairs:
        if str(dev_id) not in fleet.devices:
            continue
        uptime_s = _num(_find(d, "uptime"))
        up_since = _when(_find(d, "up_since", "boot", "started"))
        if up_since is None and uptime_s is not None:
            up_since = datetime.fromtimestamp(fleet.taken_at.timestamp() - uptime_s, UTC)
        fleet.system[str(dev_id)] = SystemStatus(
            cpu_load_pct=_num(_find(d, "cpu_load", "cpuload", "load")),
            cpu_temp_c=_num(_find(d, "temp")),
            up_since=up_since,
        )


def apply_events(fleet: Fleet, raw: Any) -> None:
    """`get_current_or_next_cms_events_for_devices` → the next or current event per device."""
    items = raw.get("devices", raw.get("events", raw)) if isinstance(raw, dict) else raw
    pairs = (
        items.items()
        if isinstance(items, dict)
        else ((d.get("device_id") or d.get("Id") or d.get("id"), d) for d in items or () if isinstance(d, dict))
    )
    for dev_id, d in pairs:
        if not isinstance(d, dict):
            continue
        ev = d.get("event") or d.get("occurrence") or (d if "start" in d or "start_time" in d else None)
        if not isinstance(ev, dict):
            continue  # no `event` key means nothing scheduled
        start = _when(ev.get("start") or ev.get("start_time") or ev.get("starts_at") or _find(ev, "start"))
        if start is None:
            continue
        fleet.events[str(dev_id)] = Event(
            device_id=str(dev_id),
            title=str(ev.get("title") or ev.get("name") or "Scheduled class"),
            start=start,
            end=_when(ev.get("end") or ev.get("end_time") or ev.get("ends_at") or _find(ev, "end")),
            id=str(ev.get("id") or ev.get("event_id") or ev.get("occurrence_id") or ""),
        )
