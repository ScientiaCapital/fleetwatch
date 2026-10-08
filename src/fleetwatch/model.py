"""The fleet as the agent sees it. Vendor adapters (src/fleetwatch/epiphan/) fill these in."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Priority(str, Enum):
    FIX_FIRST = "Fix first"
    FIX_SOON = "Fix soon"
    WHEN_CONVENIENT = "When convenient"


class RoomState(str, Enum):
    OFFLINE = "offline"
    LIVE = "live"  # recording or streaming now
    PRE_CLASS = "pre_class"  # an event starts within the lead window
    IDLE = "idle"


@dataclass
class Channel:
    id: str
    name: str
    warnings: list[str] = field(default_factory=list)
    recording: bool = False


@dataclass
class Device:
    id: str
    name: str
    model: str = ""
    group: str = ""
    online: bool = True
    firmware: str = ""
    warnings: list[str] = field(default_factory=list)
    channels: dict[str, Channel] = field(default_factory=dict)

    @property
    def is_camera(self) -> bool:
        return "ec20" in self.model.lower()

    @property
    def recording(self) -> bool:
        return any(c.recording for c in self.channels.values())


@dataclass
class Event:
    device_id: str
    title: str
    start: datetime
    end: datetime | None = None
    id: str = ""

    @property
    def key(self) -> str:
        return f"{self.device_id}:{self.id or self.start.isoformat()}"


@dataclass(frozen=True)
class Endpoint:
    """A stream destination on the team: its ID, its name (untrusted text) and its host. Never the stream key and
    never the full URL: the key is part of a URL's path, so only the host is kept."""

    id: str
    name: str
    host: str = ""


@dataclass
class SystemStatus:
    cpu_load_pct: float | None = None
    cpu_temp_c: float | None = None
    up_since: datetime | None = None


@dataclass
class Fleet:
    taken_at: datetime
    devices: dict[str, Device] = field(default_factory=dict)
    events: dict[str, Event] = field(default_factory=dict)  # next or current event per device id
    system: dict[str, SystemStatus] = field(default_factory=dict)
    # The team's stream destinations by ID. None means they weren't read (or couldn't be), which is different from
    # an empty team. Only the approval page and the write executor read them.
    endpoints: dict[str, Endpoint] | None = None


@dataclass(frozen=True)
class Finding:
    key: str  # stable across heartbeats, so the same problem is only posted once
    priority: Priority
    device_id: str
    device_name: str
    what: str  # plain words: "No picture on Program"
    impact: str = ""  # what it means for the next event
    fix: str = ""
    fyi: bool = False  # routine notes (storage); never counted as a problem


@dataclass(frozen=True)
class SweepResult:
    """One nightly sweep: a summary for people and a row of history. Names are untrusted text."""

    at: datetime
    devices: int
    online: int
    offline: tuple[str, ...] = ()
    behind: tuple[tuple[str, str, str], ...] = ()  # (name, firmware, newest in its family)
    newly_offline: tuple[str, ...] = ()
    back_online: tuple[str, ...] = ()
    posted_at: datetime | None = None
    id: int | None = None


@dataclass(frozen=True)
class Readiness:
    event: Event
    device_name: str
    verdict: str  # "Ready" | "Ready, with notes" | "Not ready"
    notes: tuple[str, ...] = ()
