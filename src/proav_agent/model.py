"""The fleet as the agent sees it. Vendor adapters (src/proav_agent/epiphan/) fill these in."""

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
    PRE_CLASS = "pre_class"  # a class starts within the lead window
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


@dataclass(frozen=True)
class Finding:
    key: str  # stable across heartbeats, so the same problem is only posted once
    priority: Priority
    device_id: str
    device_name: str
    what: str  # plain words: "No picture on Program"
    impact: str = ""  # what it means for the next class
    fix: str = ""
    fyi: bool = False  # routine notes (storage); never counted as a problem


@dataclass(frozen=True)
class Readiness:
    event: Event
    device_name: str
    verdict: str  # "Ready" | "Ready, with notes" | "Not ready"
    notes: tuple[str, ...] = ()
