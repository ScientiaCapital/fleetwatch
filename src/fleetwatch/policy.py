"""policy.yaml (how the agent behaves) and tool_policy.yaml (which tools it may call)."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import time
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType

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

# observe: watch and report. propose: the v0.2 assistant may suggest changes for a person to approve. Nothing reads
# propose mode yet, and neither mode lets Fleetwatch run a write: guard() refuses every write tool in both.
AUTONOMY = ("observe", "propose")


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
    def proposes(self) -> bool:
        """True when policy.yaml says `autonomy: propose`. Nothing reads it yet; the v0.2 assistant will."""
        return self.autonomy == "propose"

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

# `propose` (v0.2, docs/design/approved-writes.md): which write tools the assistant may *suggest*, for a person to
# approve. Configuration only: nothing here runs a write, and guard() still refuses every write tool.
PENDING = "pending-live-run"  # argument names not known until the first live run: loads, but can't be proposed
FIELD_TYPES = ("string", "integer", "boolean", "enum", "list")
_FIELD_KEYS = {
    "string": {"type", "required", "pattern", "max_length"},
    "integer": {"type", "required", "minimum", "maximum"},
    "boolean": {"type", "required"},
    "enum": {"type", "required", "values"},
    "list": {"type", "required", "items", "max_items"},
}
_ENTRY_KEYS = {"max_targets", "disruptive", "schema"}
_SCHEMA_KEYS = {"version", "target", "fields"}
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True)
class FieldSpec:
    type: str
    required: bool = False
    pattern: str | None = None  # string: the whole value must match (an ID format, for example)
    max_length: int | None = None
    minimum: int | None = None
    maximum: int | None = None
    values: tuple[str, ...] = ()  # enum
    items: "FieldSpec | None" = None  # list
    max_items: int | None = None


@dataclass(frozen=True)
class ArgSchema:
    version: int
    target: str  # the field that names the devices a change touches
    fields: Mapping[str, FieldSpec]


@dataclass(frozen=True)
class ProposeRule:
    max_targets: int
    disruptive: bool
    schema: ArgSchema | None  # None: pending until the first live run

    @property
    def pending(self) -> bool:
        return self.schema is None


@dataclass(frozen=True)
class ToolPolicy:
    read: frozenset[str]
    write: frozenset[str]
    disruptive: frozenset[str]
    propose: Mapping[str, ProposeRule] = field(default_factory=lambda: MappingProxyType({}))

    def is_read(self, tool: str) -> bool:
        return tool in self.read

    def proposable(self, tool: str) -> bool:
        """May the v0.2 assistant suggest this tool? Only a listed write tool with a reviewed (non-pending) schema."""
        rule = self.propose.get(tool)
        return rule is not None and not rule.pending and tool in self.write and tool not in self.read

    def is_disruptive(self, tool: str) -> bool:
        """On the disruptive list, or not reviewed: not listed under `propose`, pending, or marked disruptive."""
        rule = self.propose.get(tool)
        return tool in self.disruptive or rule is None or rule.pending or rule.disruptive


def _check_value(spec: FieldSpec, value, where: str) -> None:
    kind = spec.type
    if kind in ("string", "enum"):
        if not isinstance(value, str):
            raise ValueError(f"{where} must be text, not {type(value).__name__}")
        if kind == "enum" and value not in spec.values:
            raise ValueError(f"{where} must be one of {', '.join(spec.values)}, not {value!r}")
        if spec.max_length is not None and len(value) > spec.max_length:
            raise ValueError(f"{where} is longer than {spec.max_length} characters")
        if spec.pattern is not None and not re.fullmatch(spec.pattern, value):  # fullmatch: no trailing newline
            raise ValueError(f"{where} isn't in the expected format: {value!r}")
    elif kind == "integer":
        if type(value) is not int:  # bool is an int subclass: refused
            raise ValueError(f"{where} must be a whole number, not {value!r}")
        if spec.minimum is not None and value < spec.minimum:
            raise ValueError(f"{where} must be at least {spec.minimum}")
        if spec.maximum is not None and value > spec.maximum:
            raise ValueError(f"{where} must be at most {spec.maximum}")
    elif kind == "boolean":
        if type(value) is not bool:
            raise ValueError(f"{where} must be true or false, not {value!r}")
    elif kind == "list":
        if not isinstance(value, list):
            raise ValueError(f"{where} must be a list, not {type(value).__name__}")
        if spec.max_items is not None and len(value) > spec.max_items:
            raise ValueError(f"{where} may hold at most {spec.max_items}, not {len(value)}")
        assert spec.items is not None  # the loader requires items on every list
        for i, item in enumerate(value):
            _check_value(spec.items, item, f"{where}[{i}]")


def check_arguments(tool: str, rule: ProposeRule, arguments) -> tuple[str, ...]:
    """Check proposed arguments against the tool's reviewed schema and return the target device IDs.

    Refuses (ValueError, in plain words) a pending schema, anything that isn't a mapping, a missing required field,
    a field the schema doesn't name, a wrong type or format, an empty target, and more targets than max_targets.
    Checks only: it never runs anything, and guard() still refuses every write tool."""
    if rule.schema is None:
        raise ValueError(f"{tool}: its schema is still {PENDING}, so it can't be proposed")
    schema = rule.schema
    if not isinstance(arguments, dict):
        raise ValueError(f"{tool}: the arguments must be a mapping of name to value")  # noqa: TRY004
    if extra := sorted(map(str, set(arguments) - set(schema.fields))):
        raise ValueError(f"{tool}: arguments it doesn't take: {', '.join(extra)}")
    for name, spec in schema.fields.items():
        if name not in arguments:
            if spec.required:
                raise ValueError(f"{tool}: {name} is required")
            continue
        _check_value(spec, arguments[name], f"{tool}: {name}")
    value = arguments[schema.target]
    targets = tuple(value) if isinstance(value, list) else (value,)
    if not targets:
        raise ValueError(f"{tool}: {schema.target} must name at least one device")
    if len(targets) > rule.max_targets:
        raise ValueError(f"{tool}: one proposal may touch at most {rule.max_targets} device(s), not {len(targets)}")
    return targets


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
    autonomy = raw.get("autonomy", "observe")
    if not isinstance(autonomy, str) or autonomy not in AUTONOMY:
        raise ValueError(f"autonomy must be observe or propose, not {autonomy!r}")
    vertical = str(raw.get("vertical", "events"))
    if vertical not in VERTICALS:
        raise ValueError(f"vertical must be one of {', '.join(VERTICALS)}, not {vertical!r}")
    quiet = raw.get("quiet_hours") or {}
    scope = raw.get("scope") or {}
    slack = raw.get("slack") or {}
    return Policy(
        autonomy=autonomy,
        dry_run=True,  # Fleetwatch never runs a write, whatever the file says
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


def _positive_int(value, what: str, *, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:  # bool is an int subclass, so `true` is refused here too
        raise ValueError(f"{what} must be a whole number of at least {minimum}, not {value!r}")
    return value


def _field(spec, where: str, *, nested: bool = False) -> FieldSpec:
    if not isinstance(spec, dict):
        raise ValueError(f"{where} must be a mapping")  # noqa: TRY004  (same as other policy errors)
    kind = spec.get("type")
    if kind not in FIELD_TYPES:
        raise ValueError(f"{where}: type must be one of {', '.join(FIELD_TYPES)}, not {kind!r}")
    allowed = _FIELD_KEYS[kind] - ({"required"} if nested else set())
    if extra := sorted(map(str, set(spec) - allowed)):
        raise ValueError(f"{where}: unknown keys for a {kind}: {', '.join(extra)}")
    required = spec.get("required", False)
    if type(required) is not bool:
        raise ValueError(f"{where}: required must be true or false, not {required!r}")
    pattern = spec.get("pattern")
    if pattern is not None:
        if not isinstance(pattern, str) or not pattern:
            raise ValueError(f"{where}: pattern must be a regular expression")
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f"{where}: pattern doesn't compile: {e}") from None
    values: tuple[str, ...] = ()
    if kind == "enum":
        raw_values = spec.get("values")
        if not isinstance(raw_values, list) or not raw_values or not all(isinstance(v, str) for v in raw_values):
            raise ValueError(f"{where}: an enum needs a non-empty list of text values")
        values = tuple(raw_values)
    items = None
    if kind == "list":
        if nested:
            raise ValueError(f"{where}: a list can't hold lists")
        if "items" not in spec:
            raise ValueError(f"{where}: a list needs items")
        items = _field(spec["items"], f"{where}.items", nested=True)
    for key in ("minimum", "maximum"):
        if spec.get(key) is not None and type(spec[key]) is not int:
            raise ValueError(f"{where}.{key} must be a whole number, not {spec[key]!r}")
    max_length, max_items = spec.get("max_length"), spec.get("max_items")
    return FieldSpec(
        type=kind,
        required=required,
        pattern=pattern,
        max_length=None if max_length is None else _positive_int(max_length, f"{where}.max_length"),
        minimum=spec.get("minimum"),
        maximum=spec.get("maximum"),
        values=values,
        items=items,
        max_items=None if max_items is None else _positive_int(max_items, f"{where}.max_items"),
    )


def _schema(raw, where: str, max_targets: int) -> ArgSchema | None:
    if raw == PENDING:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: schema must be a mapping, or {PENDING} until the first live run")  # noqa: TRY004
    if extra := sorted(map(str, set(raw) - _SCHEMA_KEYS)):
        raise ValueError(f"{where}: unknown schema keys: {', '.join(extra)}")
    version = _positive_int(raw.get("version"), f"{where}: schema version")
    fields_raw = raw.get("fields")
    if not isinstance(fields_raw, dict) or not fields_raw:
        raise ValueError(f"{where}: schema needs at least one field under fields")
    fields: dict[str, FieldSpec] = {}
    for name, spec in fields_raw.items():
        if not isinstance(name, str) or not _NAME.match(name):
            raise ValueError(f"{where}: field names are lower-case letters, digits and _, not {name!r}")
        fields[name] = _field(spec, f"{where}.fields.{name}")
    target = raw.get("target")
    if target not in fields:
        raise ValueError(f"{where}: target must name one of the fields, not {target!r}")
    t = fields[target]
    ids = t.items if t.type == "list" else t
    if not t.required or ids is None or ids.type != "string" or not ids.pattern:
        raise ValueError(f"{where}: target {target!r} must be required text (or a list of text) with an ID pattern")
    if t.type == "list" and t.max_items is not None and t.max_items > max_targets:
        raise ValueError(f"{where}: {target}.max_items ({t.max_items}) is more than max_targets ({max_targets})")
    return ArgSchema(version=version, target=target, fields=MappingProxyType(fields))


def _propose(
    raw, where: str, read: frozenset[str], write: frozenset[str], disruptive: frozenset[str]
) -> Mapping[str, ProposeRule]:
    if raw is None:
        return MappingProxyType({})
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: propose must be a mapping of tool name to its rule")  # noqa: TRY004
    rules: dict[str, ProposeRule] = {}
    for tool, entry in raw.items():
        at = f"{where}: propose.{tool}"
        if tool in read or str(tool).startswith(READ_PREFIXES):
            raise ValueError(f"{at}: a read tool can't be proposed")
        if tool not in write:
            raise ValueError(f"{at}: only tools on the write list can be proposed")
        if not isinstance(entry, dict):
            raise ValueError(f"{at} must be a mapping with max_targets, disruptive and schema")  # noqa: TRY004
        if extra := sorted(map(str, set(entry) - _ENTRY_KEYS)):
            raise ValueError(f"{at}: unknown keys: {', '.join(extra)}")
        if "max_targets" not in entry:
            raise ValueError(f"{at}: max_targets is required")
        max_targets = _positive_int(entry["max_targets"], f"{at}.max_targets")
        flag = entry.get("disruptive", True)  # a tool that hasn't been reviewed counts as disruptive
        if type(flag) is not bool:
            raise ValueError(f"{at}: disruptive must be true or false, not {flag!r}")
        if tool in disruptive and not flag:
            raise ValueError(f"{at}: is on the disruptive list, so it can't say disruptive: false")
        if "schema" not in entry:
            raise ValueError(f"{at}: schema is required ({PENDING} until the first live run)")
        rules[str(tool)] = ProposeRule(max_targets, flag, _schema(entry["schema"], at, max_targets))
    return MappingProxyType(rules)


def _parse_tool_policy(text: str, where: str, base: ToolPolicy | None = None) -> ToolPolicy:
    """Parse and check one file. With `base` (a narrowing file), write and disruptive come from `base`."""
    raw = yaml.safe_load(text) or {}
    read = frozenset(str(t) for t in raw.get("read") or ())
    write = base.write if base else frozenset(str(t) for t in raw.get("write") or ())
    disruptive = base.disruptive if base else frozenset(str(t) for t in raw.get("disruptive") or ())
    if read & write:
        raise ValueError(f"{where}: tools listed as both read and write: {sorted(read & write)}")
    if leaked := sorted(read & KNOWN_WRITE_TOOLS):
        raise ValueError(f"{where}: write tools on the read list: {', '.join(leaked)}")
    if odd := sorted(t for t in read if not t.startswith(READ_PREFIXES)):
        raise ValueError(f"{where}: read tools must start with get_ or kb_, not: {', '.join(odd)}")
    if stray := sorted(disruptive - write):
        raise ValueError(f"{where}: disruptive tools must be on the write list: {', '.join(stray)}")
    propose = _propose(raw.get("propose"), where, read, write, disruptive)
    return ToolPolicy(read=read, write=write, disruptive=disruptive, propose=propose)


def load_tool_policy(path: Path) -> ToolPolicy:
    """One tool_policy.yaml, checked: no known write tool, and nothing but get_ and kb_ tools, under `read`; and
    every `propose` entry is a write tool with max_targets, a disruptive flag and a well-formed (or pending) schema."""
    return _parse_tool_policy(path.read_text(), str(path))


def _packaged_text() -> str:
    return files("fleetwatch").joinpath("tool_policy.yaml").read_text()


def _narrow_propose(base: ToolPolicy, mine: ToolPolicy, where: str) -> None:
    """A narrowing file may drop propose tools, lower max_targets, turn disruptive on, or set a schema back to
    pending. It can't add a tool, raise max_targets, turn disruptive off, or give a tool a new or different schema."""
    if extra := sorted(set(mine.propose) - set(base.propose)):
        raise ValueError(f"{where}: can only remove tools from Fleetwatch's propose list, not add: {', '.join(extra)}")
    for tool, rule in mine.propose.items():
        was = base.propose[tool]
        if rule.max_targets > was.max_targets:
            raise ValueError(
                f"{where}: propose.{tool}.max_targets can only go down (Fleetwatch allows {was.max_targets})"
            )
        if was.disruptive and not rule.disruptive:
            raise ValueError(f"{where}: propose.{tool} is disruptive in Fleetwatch's file; it can't be turned off")
        if rule.schema is not None and not _narrower_schema(rule.schema, was.schema):
            raise ValueError(f"{where}: propose.{tool}.schema must match Fleetwatch's, or be {PENDING}")


def _narrower_schema(mine: ArgSchema, base: ArgSchema | None) -> bool:
    """Same schema as `base`, except the target list's max_items may be lower (to go with a lower max_targets)."""
    if base is None:
        return False
    t, b = mine.fields.get(mine.target), base.fields.get(base.target)
    if t is None or b is None or t.type != "list" or b.type != "list" or t.max_items is None:
        return mine == base
    if b.max_items is not None and t.max_items > b.max_items:
        return False
    same_cap = MappingProxyType({**mine.fields, mine.target: replace(t, max_items=b.max_items)})
    return replace(mine, fields=same_cap) == base


def load_tools(narrow: Path | None = None) -> ToolPolicy:
    """The tool lists that ship inside the package, whatever the working directory. `narrow`
    (FLEETWATCH_TOOL_POLICY_FILE) may remove read or propose tools, never add or widen one; write and disruptive
    come from the package. A narrowing file with no `propose` section proposes nothing."""
    base = _parse_tool_policy(_packaged_text(), "packaged tool_policy.yaml")
    if narrow is None:
        return base
    mine = _parse_tool_policy(narrow.read_text(), str(narrow), base)
    if extra := sorted(mine.read - base.read):
        raise ValueError(f"{narrow}: can only remove tools from Fleetwatch's read list, not add: {', '.join(extra)}")
    _narrow_propose(base, mine, str(narrow))
    return mine
