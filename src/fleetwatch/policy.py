"""policy.yaml (how the agent behaves) and tool_policy.yaml (which tools it may call)."""

from dataclasses import dataclass, field
from datetime import time
from importlib.resources import files
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Thresholds:
    cpu_load_pct: float = 90
    cpu_temp_c: float = 80
    recent_reboot_minutes: int = 30


# What a scheduled recording is called where Fleetwatch runs: (singular, plural). Edge itself says "event".
VERTICALS: dict[str, tuple[str, str]] = {
    "events": ("event", "events"),
    "education": ("class", "classes"),
    "business": ("meeting", "meetings"),
    "courts": ("hearing", "hearings"),
    "worship": ("service", "services"),
}


@dataclass(frozen=True)
class Policy:
    autonomy: str = "observe"
    dry_run: bool = True
    heartbeat_seconds: int = 180
    lead_minutes: int = 30
    vertical: str = "events"
    remind_after_minutes: int = 240
    quiet_start: time | None = None
    quiet_end: time | None = None
    groups: tuple[str, ...] = ()
    exclude_devices: tuple[str, ...] = ()
    thresholds: Thresholds = field(default_factory=Thresholds)
    sweep_at: time | None = None  # nightly sweep, local time; None turns it off
    # Who may use /fleetwatch in Slack. Both empty means nobody.
    slack_allowed_user_ids: tuple[str, ...] = ()
    slack_allowed_usergroup: str | None = None

    @property
    def event_word(self) -> str:
        return VERTICALS[self.vertical][0]

    @property
    def events_word(self) -> str:
        return VERTICALS[self.vertical][1]

    def in_quiet_hours(self, now: time) -> bool:
        if self.quiet_start is None or self.quiet_end is None:
            return False
        if self.quiet_start <= self.quiet_end:
            return self.quiet_start <= now < self.quiet_end
        return now >= self.quiet_start or now < self.quiet_end  # window crosses midnight


# Epiphan write tools known today. Checked independently of tool_policy.yaml's own write list, so an edit that moves
# one onto the read list is refused at load even if it was also dropped from the file's write list. `doctor` uses
# the same set.
KNOWN_WRITE_TOOLS = frozenset(
    {
        "batch_recording",
        "batch_reboot",
        "batch_firmware_update",
        "apply_team_preset",
        "switch_device_to_cms",
        "start_stream_endpoint",
        "stop_stream_endpoint",
        "create_stream_endpoint",
        "update_stream_endpoint",
        "delete_stream_endpoint",
        "create_cms_event",
        "update_cms_event",
        "delete_cms_event",
        "cms_event_action",
        "confirm_cms_event_on_device",
    }
)
READ_PREFIXES = ("get_", "kb_")


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


def _ids(value, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"slack.{key} must be a list of Slack IDs, like [U012ABCDEF]")  # noqa: TRY004  (same as other policy errors)
    return tuple(str(v).strip() for v in value if str(v).strip())


def load_policy(path: Path) -> Policy:
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    raw = raw or {}
    if raw.get("autonomy", "observe") != "observe":
        raise ValueError("v0.1 supports autonomy: observe only")
    vertical = str(raw.get("vertical", "events"))
    if vertical not in VERTICALS:
        raise ValueError(f"vertical must be one of {', '.join(VERTICALS)}, not {vertical!r}")
    quiet = raw.get("quiet_hours") or {}
    scope = raw.get("scope") or {}
    slack = raw.get("slack") or {}
    return Policy(
        autonomy="observe",
        dry_run=True,  # v0.1 never writes, whatever the file says
        heartbeat_seconds=int(raw.get("heartbeat_seconds", 180)),
        lead_minutes=int(raw.get("lead_minutes", raw.get("preclass_lead_minutes", 30))),  # old name still works
        vertical=vertical,
        remind_after_minutes=int(raw.get("remind_after_minutes", 240)),
        quiet_start=_hhmm(quiet.get("start")),
        quiet_end=_hhmm(quiet.get("end")),
        groups=tuple(scope.get("groups") or ()),
        exclude_devices=tuple(scope.get("exclude_devices") or ()),
        thresholds=Thresholds(**(raw.get("thresholds") or {})),
        sweep_at=_hhmm(raw.get("sweep_at", "03:00")),
        slack_allowed_user_ids=_ids(slack.get("allowed_user_ids"), "allowed_user_ids"),
        slack_allowed_usergroup=str(slack.get("allowed_usergroup") or "").strip() or None,
    )


def _parse_tool_policy(text: str, where: str) -> ToolPolicy:
    raw = yaml.safe_load(text) or {}
    read = frozenset(str(t) for t in raw.get("read") or ())
    write = frozenset(str(t) for t in raw.get("write") or ())
    if read & write:
        raise ValueError(f"{where}: tools listed as both read and write: {sorted(read & write)}")
    if leaked := sorted(read & KNOWN_WRITE_TOOLS):
        raise ValueError(f"{where}: write tools on the read list: {', '.join(leaked)}")
    if odd := sorted(t for t in read if not t.startswith(READ_PREFIXES)):
        raise ValueError(f"{where}: read tools must start with get_ or kb_, not: {', '.join(odd)}")
    return ToolPolicy(read=read, write=write, disruptive=frozenset(raw.get("disruptive") or ()))


def load_tool_policy(path: Path) -> ToolPolicy:
    """One tool_policy.yaml, checked: no known write tool, and nothing but get_ and kb_ tools, under `read`."""
    return _parse_tool_policy(path.read_text(), str(path))


def load_tools(narrow: Path | None = None) -> ToolPolicy:
    """The read list that ships inside the package, whatever the working directory. `narrow`
    (FLEETWATCH_TOOL_POLICY_FILE) may remove read tools, never add one; write and disruptive come from the package."""
    packaged = files("fleetwatch").joinpath("tool_policy.yaml").read_text()
    base = _parse_tool_policy(packaged, "packaged tool_policy.yaml")
    if narrow is None:
        return base
    mine = load_tool_policy(narrow)
    if extra := sorted(mine.read - base.read):
        raise ValueError(f"{narrow}: can only remove tools from Fleetwatch's read list, not add: {', '.join(extra)}")
    return ToolPolicy(read=mine.read, write=base.write, disruptive=base.disruptive)
