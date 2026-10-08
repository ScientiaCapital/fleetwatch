"""Turn Epiphan MCP tool results into the shared data model. Tolerant of missing keys: the agent must keep
running when a field is absent, and must never invent state it didn't read."""

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from fleetwatch.model import Channel, Device, Endpoint, Event, Fleet, SystemStatus

log = logging.getLogger(__name__)

STORAGE_WARNINGS = frozenset({"disk_space_error", "no_storage_detected"})
_ID_KEYS = frozenset({"device_id", "id", "Id", "deviceId"})
_warned: set[str] = set()


def _unread(tool: str) -> None:
    """A non-empty result that parsed to nothing: say so once per tool, naming the tool and never the content."""
    if tool not in _warned:
        _warned.add(tool)
        log.warning("%s: got a result in a shape Fleetwatch doesn't know; ignoring it (warned once)", tool)


def reset_warnings() -> None:
    _warned.clear()


def _has_content(d: Any) -> bool:
    """True when an entry carries anything besides its id: an empty or all-null entry means "nothing here"."""
    return isinstance(d, dict) and any(v not in (None, "", [], {}) for k, v in d.items() if k not in _ID_KEYS)


def device_items(raw: Any) -> list | None:
    """The device list in a `get_devices_in_my_team` result, or None when the result isn't one: text instead of
    JSON, a shape we don't know, or entries none of which has an `Id`. None is a failed read, never 0 devices."""
    items = raw.get("devices") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return None
    if items and not any(isinstance(d, dict) and d.get("Id") for d in items):
        return None
    return items


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
    tool = "get_recorder_status_for_devices"
    if not isinstance(items, (dict, list)):
        if items:
            _unread(tool)
        return
    read = 0
    pairs = (
        items.items()
        if isinstance(items, dict)
        else ((d.get("device_id") or d.get("Id") or d.get("id"), d) for d in items or () if isinstance(d, dict))
    )
    for dev_id, d in pairs:
        dev = fleet.devices.get(str(dev_id))
        if dev is None or not isinstance(d, dict):
            continue
        read += 1
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
    if items and not read:
        _unread(tool)


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
    tool = "get_system_status_for_devices"
    if not isinstance(items, (dict, list)):
        if items:
            _unread(tool)
        return
    pairs = (
        items.items()
        if isinstance(items, dict)
        else ((d.get("device_id") or d.get("Id") or d.get("id"), d) for d in items or () if isinstance(d, dict))
    )
    read = 0
    for dev_id, d in pairs:
        if str(dev_id) not in fleet.devices:
            continue
        status = SystemStatus(
            cpu_load_pct=_num(_find(d, "cpu_load", "cpuload", "load")),
            cpu_temp_c=_num(_find(d, "temp")),
            up_since=_up_since(d, fleet.taken_at),
        )
        fleet.system[str(dev_id)] = status
        read += status != SystemStatus()
    if items and not read:
        _unread(tool)


def _uptime_values(d: Any) -> list[Any]:
    """Every scalar under a key containing `uptime`, nested dicts included."""
    out: list[Any] = []
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, dict):
                out += _uptime_values(v)
            elif "uptime" in str(k).lower() and v not in (None, ""):
                out.append(v)
    return out


def _up_since(d: Any, taken_at: datetime) -> datetime | None:
    """When the device last started. Epiphan describes it as an "uptime start time (RFC3339)"; older shapes give
    seconds of uptime, or a start time under up_since / boot / started."""
    named = _when(_find(d, "up_since", "boot", "started"))
    if named is not None:
        return named
    for v in _uptime_values(d):
        seconds = _num(v)
        if seconds is not None:
            return datetime.fromtimestamp(taken_at.timestamp() - seconds, UTC)
        if isinstance(v, str) and (when := _when(v)) is not None:
            return when
    return None


def apply_events(fleet: Fleet, raw: Any) -> None:
    """`get_current_or_next_cms_events_for_devices` → the next or current event per device."""
    items = raw.get("devices", raw.get("events", raw)) if isinstance(raw, dict) else raw
    tool = "get_current_or_next_cms_events_for_devices"
    if not isinstance(items, (dict, list)):
        if items:
            _unread(tool)
        return
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
            if "event" not in d and "occurrence" not in d and _has_content(d):
                _unread(tool)  # something is there, but not an event we can read
            continue  # an empty or null `event` means nothing scheduled
        start = _when(ev.get("start") or ev.get("start_time") or ev.get("starts_at") or _find(ev, "start"))
        if start is None:
            _unread(tool)
            continue
        fleet.events[str(dev_id)] = Event(
            device_id=str(dev_id),
            title=str(ev.get("title") or ev.get("name") or "Scheduled event"),
            start=start,
            end=_when(ev.get("end") or ev.get("end_time") or ev.get("ends_at") or _find(ev, "end")),
            id=str(ev.get("id") or ev.get("event_id") or ev.get("occurrence_id") or ""),
        )


_URL_KEYS = ("url", "Url", "URL", "rtmp_url", "server", "address", "Address")


def _host(entry: dict[str, Any]) -> str:
    """The host of an endpoint's URL, and nothing else: a stream key lives in the URL's path or query."""
    for key in _URL_KEYS:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            try:
                return urlsplit(text if "//" in text else f"//{text}").hostname or ""
            except ValueError:
                return ""
    return ""


def parse_stream_endpoints(raw: Any) -> dict[str, Endpoint] | None:
    """`get_stream_endpoints` → the team's destinations by ID, each with its name and host only. None when the
    result isn't a list of entries with IDs (text, or a shape we don't know): a failed read, never "no endpoints"."""
    items = raw
    if isinstance(raw, dict):
        items = next((raw[k] for k in ("stream_endpoints", "endpoints", "streams", "items") if k in raw), None)
    if not isinstance(items, list):
        return None
    out: dict[str, Endpoint] = {}
    for d in items:
        if not isinstance(d, dict):
            continue
        sid = d.get("id") or d.get("Id") or d.get("stream_id") or d.get("StreamID")
        if not sid:
            return None
        out[str(sid)] = Endpoint(str(sid), str(d.get("name") or d.get("Name") or ""), _host(d))
    if items and not out:
        return None
    return out
