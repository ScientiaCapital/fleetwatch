"""policy.yaml (how the agent behaves) and tool_policy.yaml (which tools it may call)."""

from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Thresholds:
    cpu_load_pct: float = 90
    cpu_temp_c: float = 80
    recent_reboot_minutes: int = 30


@dataclass(frozen=True)
class Policy:
    autonomy: str = "observe"
    dry_run: bool = True
    heartbeat_seconds: int = 180
    preclass_lead_minutes: int = 30
    remind_after_minutes: int = 240
    quiet_start: time | None = None
    quiet_end: time | None = None
    groups: tuple[str, ...] = ()
    exclude_devices: tuple[str, ...] = ()
    thresholds: Thresholds = field(default_factory=Thresholds)

    def in_quiet_hours(self, now: time) -> bool:
        if self.quiet_start is None or self.quiet_end is None:
            return False
        if self.quiet_start <= self.quiet_end:
            return self.quiet_start <= now < self.quiet_end
        return now >= self.quiet_start or now < self.quiet_end  # window crosses midnight


@dataclass(frozen=True)
class ToolPolicy:
    read: frozenset[str]
    write: frozenset[str]
    disruptive: frozenset[str]

    def is_read(self, tool: str) -> bool:
        return tool in self.read


def _hhmm(value: str | None) -> time | None:
    if not value:
        return None
    hours, minutes = str(value).split(":")
    return time(int(hours), int(minutes))


def load_policy(path: Path) -> Policy:
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    raw = raw or {}
    if raw.get("autonomy", "observe") != "observe":
        raise ValueError("v0.1 supports autonomy: observe only")
    quiet = raw.get("quiet_hours") or {}
    scope = raw.get("scope") or {}
    return Policy(
        autonomy="observe",
        dry_run=True,  # v0.1 never writes, whatever the file says
        heartbeat_seconds=int(raw.get("heartbeat_seconds", 180)),
        preclass_lead_minutes=int(raw.get("preclass_lead_minutes", 30)),
        remind_after_minutes=int(raw.get("remind_after_minutes", 240)),
        quiet_start=_hhmm(quiet.get("start")),
        quiet_end=_hhmm(quiet.get("end")),
        groups=tuple(scope.get("groups") or ()),
        exclude_devices=tuple(scope.get("exclude_devices") or ()),
        thresholds=Thresholds(**(raw.get("thresholds") or {})),
    )


def load_tool_policy(path: Path) -> ToolPolicy:
    raw = yaml.safe_load(path.read_text()) or {}
    read = frozenset(raw.get("read") or ())
    write = frozenset(raw.get("write") or ())
    if read & write:
        raise ValueError(f"tools listed as both read and write: {sorted(read & write)}")
    return ToolPolicy(read=read, write=write, disruptive=frozenset(raw.get("disruptive") or ()))
