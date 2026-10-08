"""Turn a real capture (`fleetwatch digest --capture DIR`) into a fixture set that is safe to share.

    python -m fleetwatch.epiphan.anonymize CAPTURE_DIR OUT_DIR

It's a whitelist: only the four tools below are kept, and only the fields listed here. Names, IDs, groups, addresses,
serials and event titles are replaced with neutral ones, the same replacement everywhere, so the files still agree
with each other. Absolute times become `{{now+25m}}`-style tokens (see replay.py), so the set stays current whenever
it's replayed. Model, status, firmware, booleans and numbers are kept: that's what makes it a real shape.

Before anything is written it checks that no original name, ID, address, serial or title survives anywhere in the
output, and refuses if one does. It prints counts, never values. Read the output yourself before you commit it:
a tool can't tell that a firmware build is unreleased or that a count of devices is itself private.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TOOLS = (
    "get_devices_in_my_team",
    "get_recorder_status_for_devices",
    "get_current_or_next_cms_events_for_devices",
    "get_system_status_for_devices",
)
CHANNEL_NAMES = ("Program", "Content", "Camera 1", "Camera 2", "Camera 3", "Camera 4")
SOURCE_NAMES = {"HDMI", "SDI", "NDI 1", "NDI 2", "NDI 3", "NDI 4", "HDMI 1", "HDMI 2", "SDI 1", "SDI 2"}
_GENERIC = {n.lower() for n in CHANNEL_NAMES} | {"epiphan", "scheduled", "stopped", "online", "offline", "recording"}
# Words the replacements are made of. An original that is only a piece of these ("Camera" inside "Camera 1") is a
# common word, not an identifier, so finding it in the output isn't a leak.
_NEUTRAL_WORDS = " ".join(
    [
        *CHANNEL_NAMES,
        "Room",
        "Device",
        "Pearl Mini",
        "Pearl Nano",
        "Pearl Nexus",
        "Pearl-2",
        "EC20",
        "Venue",
        "Group",
        "Sample class",
        "Output",
        "Channel",
        "Source",
    ]
).lower()
_NOTE = (
    "Synthetic demo data for replay mode, derived from a redacted capture with every name, ID, address, serial and "
    "event title replaced. Times like {{now+25m}} are resolved when the replay runs."
)
_EPOCH = "1700000000"  # an epoch-seconds field with no meaning here, kept as the same kind of value
_MIN_LEAK_LEN = 4


class AnonymizeError(Exception):
    """Something from the original would have survived, or the capture can't be read. Never carries a value."""


class _Names:
    """One replacement per original, handed out in the order first seen, so every file agrees."""

    def __init__(self, captured_at: datetime):
        self.at = captured_at
        self.ids: dict[str, str] = {}
        self.models: dict[str, str] = {}
        self.names: dict[str, str] = {}
        self.groups: dict[str, int] = {}
        self.events: dict[str, int] = {}
        self.outputs = 0

    def dev_id(self, original: Any) -> str:
        key = str(original)
        if key not in self.ids:
            self.ids[key] = f"0a{len(self.ids) + 1:09x}"  # 11 hex characters, like a real master ID
        return self.ids[key]

    def dev_name(self, original: Any, model: str | None = None) -> str:
        key = str(original)
        if key not in self.names:
            kind = model or self.models.get(key) or "Device"
            self.names[key] = f"Room {200 + len(self.names)} {kind}"
        return self.names[key]

    def group(self, original: Any) -> int:
        return self.groups.setdefault(str(original), len(self.groups) + 1)

    def event(self, original: Any) -> int:
        return self.events.setdefault(str(original), len(self.events) + 1)

    def rel(self, value: Any) -> Any:
        """An ISO time as a `{{now±N}}` token, relative to when the capture was made. Anything else (an epoch, a
        blank) isn't a time we can place, so it becomes a neutral constant of the same kind."""
        when = _parse(value)
        if when is None:
            return _EPOCH if isinstance(value, str) and value.isdigit() else value
        minutes = round((when - self.at).total_seconds() / 60)
        if minutes == 0:
            return "{{now}}"
        sign, n = ("+" if minutes > 0 else "-"), abs(minutes)
        return f"{{{{now{sign}{n // 60}h}}}}" if n % 60 == 0 else f"{{{{now{sign}{n}m}}}}"


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _channel_name(channel_id: Any) -> str:
    text = str(channel_id)
    return CHANNEL_NAMES[(int(text) - 1) % len(CHANNEL_NAMES)] if text.isdigit() and int(text) > 0 else "Channel"


def _publisher(pub: dict[str, Any], names: _Names) -> dict[str, Any]:
    out: dict[str, Any] = {k: v for k, v in pub.items() if k in ("id", "type")}
    status = pub.get("status") if isinstance(pub.get("status"), dict) else {}
    out["status"] = {
        "since": 0,
        "state": status.get("state", "stopped"),
        "started": bool(status.get("started", False)),
        "description": "",
    }
    settings = []
    for s in pub.get("settings") or []:
        if not isinstance(s, dict):
            continue
        kept = {k: v for k, v in s.items() if k in ("id", "type", "title")}
        value = s.get("value")
        if isinstance(value, str):
            names.outputs += 1
            value = f"Output {names.outputs}"
        kept["value"] = value
        settings.append(kept)
    out["settings"] = settings
    return out


def _warning(w: dict[str, Any], names: _Names, channel_name: str | None) -> dict[str, Any]:
    wid = str(w.get("id", ""))
    data = w.get("data")
    out: dict[str, Any] = {"id": wid, "label": w.get("label", "")}
    if isinstance(data, dict):
        data = dict(data)
        if "channel_name" in data:
            data["channel_name"] = channel_name or "Channel"
        if "source_name" in data and str(data["source_name"]) not in SOURCE_NAMES:
            data["source_name"] = "HDMI"
    out["data"] = data
    message = str(w.get("message", ""))
    if wid == "channel_no_signal" and isinstance(data, dict):
        message = f"Channel '{data.get('channel_name')}' has no signal"
    elif wid == "source_no_signal" and isinstance(data, dict):
        message = f"Source '{data.get('source_name')}' has no signal"
    out["message"] = message
    return out


def _devices(raw: Any, names: _Names) -> dict[str, Any]:
    items = raw.get("devices", []) if isinstance(raw, dict) else []
    for d in items:  # first pass: model per ID, so a device's name matches what it is
        if isinstance(d, dict):
            names.models[str(d.get("Id"))] = str(d.get("Model", "Device"))
    out = []
    for d in items:
        if not isinstance(d, dict):
            continue
        group = names.group(d.get("GroupId"))
        n = names.ids.get(str(d.get("Id")))
        device: dict[str, Any] = {
            "Id": names.dev_id(d.get("Id")),
            "Name": names.dev_name(d.get("Id"), str(d.get("Model", "Device"))),
            "Model": d.get("Model", ""),
            "GroupId": f"00000000-0000-4000-8000-{group:012d}",
            "GroupName": f"Group {group}",
            "IPAddress": f"192.0.2.{(len(names.ids) % 250) + 1}",
            "SerialNumber": f"SN{len(names.ids):08d}",
            "Firmware": d.get("Firmware", ""),
            "Status": d.get("Status", ""),
            "StateTime": names.rel(d.get("StateTime")),
            "Channels": [],
            "Warnings": [],
        }
        assert n is None or n == device["Id"]  # the same original always maps to the same replacement
        for ch in d.get("Channels") or []:
            if not isinstance(ch, dict):
                continue
            cname = _channel_name(ch.get("channel_id"))
            rec = ch.get("recording_status") if isinstance(ch.get("recording_status"), dict) else {}
            device["Channels"].append(
                {
                    "channel_id": ch.get("channel_id"),
                    "name": cname,
                    "Publishers": [_publisher(p, names) for p in ch.get("Publishers") or [] if isinstance(p, dict)],
                    "recording_status": {
                        "state": rec.get("state", "stopped"),
                        "timestamp": names.rel(rec.get("timestamp")),
                        "description": rec.get("description", ""),
                    },
                    "Warnings": [_warning(w, names, cname) for w in ch.get("Warnings") or [] if isinstance(w, dict)],
                }
            )
        device["Warnings"] = [_warning(w, names, None) for w in d.get("Warnings") or [] if isinstance(w, dict)]
        out.append(device)
    return {"devices": out}


def _recorder(raw: Any, names: _Names) -> dict[str, Any]:
    out: dict[str, Any] = {}
    devices = raw.get("devices", {}) if isinstance(raw, dict) else {}
    for dev_id, d in devices.items() if isinstance(devices, dict) else ():
        if not isinstance(d, dict):
            continue
        channels = {}
        for cid, ch in (d.get("Channels") or {}).items():
            status = (ch or {}).get("RecordingStatus") or {}
            channels[str(cid)] = {
                "Name": _channel_name(cid),
                "RecordingStatus": {
                    "state": status.get("state", "stopped"),
                    "timestamp": _EPOCH,
                    "description": status.get("description", ""),
                },
            }
        out[names.dev_id(dev_id)] = {"Name": names.dev_name(dev_id), "Channels": channels}
    return {"devices": out}


def _events(raw: Any, names: _Names) -> dict[str, Any]:
    out = []
    for item in raw.get("events", []) if isinstance(raw, dict) else []:
        ev = item.get("event") if isinstance(item, dict) else None
        if not isinstance(ev, dict):
            continue
        n = names.event(ev.get("id"))
        out.append(
            {
                "device_id": names.dev_id(item.get("device_id")),
                "event": {
                    "id": f"evt-{n}",
                    "title": f"Sample class {n}",
                    "cms": ev.get("cms", ""),
                    "status": ev.get("status", ""),
                    "confirmed": bool(ev.get("confirmed", False)),
                    "start": names.rel(ev.get("start")),
                    "finish": names.rel(ev.get("finish")),
                    "devices": [
                        {
                            "device_id": names.dev_id(dv.get("device_id")),
                            "channels": [
                                {
                                    "channel_id": c.get("channel_id"),
                                    "recording": bool(c.get("recording", False)),
                                    "streaming": bool(c.get("streaming", False)),
                                }
                                for c in dv.get("channels") or []
                                if isinstance(c, dict)
                            ],
                        }
                        for dv in ev.get("devices") or []
                        if isinstance(dv, dict)
                    ],
                },
            }
        )
    return {"events": out}


def _system(raw: Any, names: _Names) -> dict[str, Any]:
    items = raw.get("status", []) if isinstance(raw, dict) else []
    return {
        "status": [
            {
                "device_id": names.dev_id(s.get("device_id")),
                "cpu_load_pct": s.get("cpu_load_pct"),
                "cpu_temp_c": s.get("cpu_temp_c"),
                "uptime_since": names.rel(s.get("uptime_since")),
            }
            for s in items
            if isinstance(s, dict)
        ]
    }


def anonymize(raw: dict[str, Any], captured_at: datetime) -> dict[str, Any]:
    """The four known tools, anonymized; any other tool in `raw` is left out."""
    names = _Names(captured_at if captured_at.tzinfo else captured_at.replace(tzinfo=UTC))
    out: dict[str, Any] = {}
    if "get_devices_in_my_team" in raw:  # first, so every ID is handed out in fleet order
        out["get_devices_in_my_team"] = _devices(raw["get_devices_in_my_team"], names)
    for tool, fn in (
        ("get_recorder_status_for_devices", _recorder),
        ("get_current_or_next_cms_events_for_devices", _events),
        ("get_system_status_for_devices", _system),
    ):
        if tool in raw:
            out[tool] = fn(raw[tool], names)
    return out


def _originals(raw: dict[str, Any]) -> list[str]:
    """Every string in the capture that identifies something: IDs, names, groups, addresses, serials, titles."""
    found: set[str] = set()

    def add(v: Any) -> None:
        if isinstance(v, str) and len(v) >= _MIN_LEAK_LEN:
            found.add(v)

    for d in (raw.get("get_devices_in_my_team") or {}).get("devices", []):
        for key in ("Id", "Name", "GroupId", "GroupName", "IPAddress", "SerialNumber"):
            add(d.get(key))
        for ch in d.get("Channels") or []:
            add(ch.get("name"))
            for pub in ch.get("Publishers") or []:
                for s in pub.get("settings") or []:
                    add(s.get("value"))
    for dev_id, d in ((raw.get("get_recorder_status_for_devices") or {}).get("devices") or {}).items():
        add(dev_id)
        add(d.get("Name"))
        for ch in (d.get("Channels") or {}).values():
            add((ch or {}).get("Name"))
    for item in (raw.get("get_current_or_next_cms_events_for_devices") or {}).get("events", []):
        add(item.get("device_id"))
        ev = item.get("event") or {}
        add(ev.get("id"))
        add(ev.get("title"))
    for s in (raw.get("get_system_status_for_devices") or {}).get("status", []):
        add(s.get("device_id"))
    return sorted(found)


def leaks(output: Any, originals: list[str]) -> list[str]:
    """Originals that appear anywhere in `output`, ignoring short or generic strings (a room can be called
    "Program" without that being a secret)."""
    text = json.dumps(output).lower()
    return [
        o
        for o in originals
        if len(o) >= _MIN_LEAK_LEN
        and o.lower() not in _GENERIC
        and o.lower() not in _NEUTRAL_WORDS
        and o.lower() in text
    ]


def write_set(src: Path, dst: Path) -> dict[str, Any]:
    """Read a capture folder, write the anonymized set to `dst`. Nothing is written if anything would leak."""
    try:
        manifest = json.loads((src / "_manifest.json").read_text())
        captured_at = datetime.fromisoformat(str(manifest["captured_at"]))
    except (OSError, ValueError, KeyError) as e:
        raise AnonymizeError(f"can't read {src}/_manifest.json ({type(e).__name__})") from None
    raw: dict[str, Any] = {}
    dropped: list[str] = []
    for path in sorted(src.glob("*.json")):
        if path.name == "_manifest.json":
            continue
        tool = path.stem
        if tool in TOOLS:
            raw[tool] = json.loads(path.read_text())
        else:
            dropped.append(tool)
    out = anonymize(raw, captured_at)
    found = leaks(out, _originals(raw))
    if found:
        raise AnonymizeError(f"{len(found)} original value(s) would survive in the output; nothing was written")
    dst.mkdir(parents=True, exist_ok=True)
    for tool, body in out.items():
        (dst / f"{tool}.json").write_text(json.dumps({"_note": _NOTE, **body}, indent=2) + "\n")
    return {"written": sorted(out), "dropped": dropped, "leaks": []}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: python -m fleetwatch.epiphan.anonymize CAPTURE_DIR OUT_DIR")
        return 2
    try:
        report = write_set(Path(args[0]).expanduser(), Path(args[1]).expanduser())
    except AnonymizeError as e:
        print(f"Refused: {e}")
        return 1
    print(f"Wrote {len(report['written'])} file(s) to {args[1]}; left out {len(report['dropped'])} other tool(s).")
    print("No original name, ID, address, serial or title survives. Read the files before you commit them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
