"""The v0.2 write executor: runs one approved change, once, through the sandbox sign-in only.

See docs/design/approved-writes.md, "Flow" steps 4-7, "Binding", "Fence: the sandbox sign-in" and "Disruptive tools".
Nothing in this version calls it yet: there is no approval page. `EpiphanClient.call()` and `guard()` still refuse
every write tool, unconditionally. This class is the only other place an MCP tool is called, and it calls exactly
one write tool per consumed approval.

`WriteExecutor.execute(record)` takes a record that `State.consume()` returned, and:

1. Claims it in the state file, once. A record that isn't byte for byte what was stored and signed, or one already
   claimed, is refused.
2. Refuses unless policy.yaml says `autonomy: propose`, the tool is under `propose` with a reviewed (not pending)
   schema of the same version, and the arguments pass `policy.check_arguments` again. The approved device IDs
   must be exactly the arguments' targets, with a channel ID ("<master>-N") counted as its master device.
3. Opens one MCP session with the sandbox sign-in, from its own token slot. The normal sign-in is never loaded: no
   static token, no normal token file. With no sandbox sign-in, nothing runs.
4. Reads through an `EpiphanClient` on that session, so every read goes through the same `guard()` and `redact()`
   as the heartbeat: the sandbox team's device list (every target device, a channel's master included, must be on
   it), each target's recording state and next event (they must match the approved fingerprint), and the team ID
   when FLEETWATCH_WRITE_TEAM_ID is set. With neither that nor FLEETWATCH_WRITE_DEVICE_IDS set, nothing runs, and a
   target not on that allowlist is refused. A disruptive change is refused while a target room is recording or
   inside the readiness window. Any failed read refuses the change.
5. Calls the write tool once, with the canonical arguments. No retry, ever. A 401, a timeout or any transport error
   is outcome `unknown`, which is final: the change may or may not have happened.
6. Records the outcome (ok, error or unknown) and audits every step, redacted. A refusal is recorded as error with
   "nothing ran".

Device, channel and event names are untrusted: only IDs are compared, and no name picks a code path.
"""

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fleetwatch.agents.room_state.rules import room_state
from fleetwatch.config import Settings
from fleetwatch.epiphan.mcp import _UNAUTHORIZED, EpiphanClient
from fleetwatch.epiphan.parse import apply_events, apply_recorder_status, device_items, parse_devices
from fleetwatch.epiphan.token_store import SANDBOX_SERVICE, TokenStore, TokenStoreError, make_token_store
from fleetwatch.fence import Fence, master_id
from fleetwatch.model import Fleet, RoomState
from fleetwatch.policy import Policy, ToolPolicy, check_arguments, load_policy, load_tools
from fleetwatch.proposals import BoundRecord, NotCanonical, parse_canonical, state_fingerprint
from fleetwatch.redact import redact
from fleetwatch.state import State

log = logging.getLogger(__name__)

SANDBOX_SLOT = "sandbox"
_TEAM_KEYS = ("team_id", "teamId", "TeamId", "TeamID")
_MESSAGE_CAP = 300


class Refused(Exception):
    """A check said no before the write was sent. `reason` is plain words for the operator and the audit log."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Outcome:
    """What happened to one approved change.

    status: ok (Epiphan reported no errors), error (Epiphan reported per-device errors, or refused the call),
    unknown (the write was sent but its result can't be known: a 401, a timeout, a dropped connection; never
    retried), or refused (a check said no, and nothing was sent). `errors` maps device ID to Epiphan's message,
    redacted."""

    status: str
    detail: str
    errors: Mapping[str, str] = field(default_factory=dict)

    @property
    def sent(self) -> bool:
        return self.status != "refused"


def sandbox_store(settings: Settings) -> TokenStore:
    """The sandbox sign-in's own token slot: FLEETWATCH_SANDBOX_TOKEN_FILE, and its own Keychain or systemd-creds
    name. Refuses a sandbox file that is the normal sign-in's file."""
    if _same_file(settings.sandbox_token_file, settings.token_file):
        raise ValueError(
            "FLEETWATCH_SANDBOX_TOKEN_FILE must be a different file from FLEETWATCH_TOKEN_FILE: "
            "the sandbox sign-in has its own slot"
        )
    return make_token_store(settings.token_store, settings.sandbox_token_file, service=SANDBOX_SERVICE)


def _same_file(a: Path, b: Path) -> bool:
    return a.expanduser().resolve() == b.expanduser().resolve()


def _clip(text: str) -> str:
    text = redact(str(text))
    return text if len(text) <= _MESSAGE_CAP else text[: _MESSAGE_CAP - 1] + "…"


class WriteExecutor:
    """Runs one consumed approval through the sandbox sign-in. Built from Settings; it only ever loads the sandbox
    token slot, so it can't be built on the normal sign-in."""

    def __init__(
        self,
        settings: Settings,
        state: State,
        *,
        policy: Policy | None = None,
        tools: ToolPolicy | None = None,
        timeout_s: float = 60,
    ):
        self._store = sandbox_store(settings)  # raises when the slot is the normal one
        self._url = settings.epiphan_mcp_url
        self._port = settings.oauth_callback_port
        self._team_id = settings.write_team_id.strip()
        self._fence = Fence.from_settings(settings)
        self._state = state
        self._policy = policy if policy is not None else load_policy(settings.policy_file)
        self._tools = tools if tools is not None else load_tools(settings.tool_policy_file)
        self._timeout = timeout_s

    async def execute(self, record: BoundRecord) -> Outcome:
        """Run one approved change, once. Never raises; every path ends in an Outcome and an audit row."""
        try:
            approval_id = self._state.claim(record)
        except Exception as e:  # noqa: BLE001 - a broken state file refuses, it never runs
            return self._refused(None, record, f"couldn't check the approval ({_clip(type(e).__name__)})")
        if approval_id is None:
            return self._refused(
                None, record, "this isn't an approved change waiting to run, or it was changed after approval"
            )
        try:
            args = self._check_policy(record)
            if not self._has_sandbox_sign_in():
                raise Refused("Sandbox sign-in: none, so no change can run (fleetwatch login --sandbox)")
        except Refused as e:
            return self._refused(approval_id, record, e.reason)
        except Exception as e:  # noqa: BLE001
            return self._refused(approval_id, record, f"a check failed: {_clip(e)}")
        return await self._run(approval_id, record, args)

    # --- before any session opens -------------------------------------------------------------------
    def _check_policy(self, record: BoundRecord) -> dict[str, Any]:
        if not self._policy.proposes:
            raise Refused("policy.yaml says autonomy: observe, so no change can run")
        if record.slot != SANDBOX_SLOT:
            raise Refused(f"this change was bound to the {record.slot!r} sign-in; changes run only on the sandbox")
        if not self._tools.proposable(record.tool):
            raise Refused(f"{record.tool} isn't a tool that can be proposed (tool_policy.yaml, propose)")
        rule = self._tools.propose[record.tool]
        schema = rule.schema
        if schema is None:  # proposable() already says no to a pending schema; kept for the type checker
            raise Refused(f"{record.tool}'s arguments aren't reviewed yet")
        if schema.version != record.schema_version:
            raise Refused(
                f"{record.tool}'s schema changed since approval (v{record.schema_version} to v{schema.version})"
            )
        try:
            args = parse_canonical(record.arguments)
        except (NotCanonical, ValueError) as e:
            raise Refused(f"the arguments can't be read: {_clip(e)}") from None
        try:
            named = check_arguments(record.tool, rule, args)  # the same validator proposals use
        except ValueError as e:
            raise Refused(f"the arguments don't fit the schema: {_clip(e)}") from None
        if len(set(named)) != len(named):
            raise Refused("a target is named twice")
        if not record.targets or len(set(record.targets)) != len(record.targets):
            raise Refused("the target device IDs are missing or repeated")
        if len(record.targets) > rule.max_targets:
            raise Refused(f"{record.tool} may touch at most {rule.max_targets} device(s)")
        if {master_id(v) for v in named} != set(record.targets):
            raise Refused("the approved devices don't match the change's arguments")
        return args

    def _has_sandbox_sign_in(self) -> bool:
        try:
            return self._store.has_tokens() and not self._store.is_dead()
        except (TokenStoreError, OSError):
            return False

    # --- the session --------------------------------------------------------------------------------
    async def _run(self, approval_id: int, record: BoundRecord, args: dict[str, Any]) -> Outcome:
        reader = EpiphanClient(
            self._url, self._tools, storage=self._store, callback_port=self._port, timeout_s=self._timeout
        )
        try:
            await reader.__aenter__()
        except Exception as e:  # noqa: BLE001
            return self._refused(approval_id, record, f"couldn't open the sandbox session: {_clip(e)}")
        try:
            try:
                await self._reads(reader, record, args)
            except Refused as e:
                return self._refused(approval_id, record, e.reason)
            except Exception as e:  # noqa: BLE001 - fail closed on anything unexpected before the write
                return self._refused(approval_id, record, f"a check failed: {_clip(e)}")
            # The one write. `reader._client` is the same sandbox session the reads used; it is never handed to
            # EpiphanClient.call(), whose guard refuses every write tool.
            outcome = await self._write(reader._client, record, args)
        finally:
            try:
                await reader.__aexit__(None, None, None)
            except Exception as e:  # noqa: BLE001 - closing never changes an outcome
                log.warning("closing the sandbox session: %s", _clip(e))
        return self._finish(approval_id, record, outcome)

    async def _reads(self, reader: EpiphanClient, record: BoundRecord, args: dict[str, Any]) -> None:
        """Fresh reads through the guarded client. Raises Refused on any failed read or failed check."""
        if not self._fence.is_set:
            raise Refused(
                "no sandbox fence is set: add the sandbox devices to FLEETWATCH_WRITE_DEVICE_IDS "
                "(or set FLEETWATCH_WRITE_TEAM_ID), so a change can't reach a team that isn't the sandbox"
            )
        now = datetime.now(UTC)
        try:
            raw = await reader.call("get_devices_in_my_team")
        except Exception as e:  # noqa: BLE001
            raise Refused(f"couldn't read the sandbox team's devices: {_clip(e)}") from None
        if device_items(raw) is None:
            raise Refused("the sandbox team's device list couldn't be read")
        self._check_team(raw)
        fleet = parse_devices(raw, now)
        missing = [t for t in record.targets if t not in fleet.devices]
        if missing:
            raise Refused(f"not on the sandbox team's device list: {', '.join(missing)}")
        if self._fence.device_ids:
            off = [t for t in record.targets if not self._fence.allows(t)]
            if off:
                raise Refused(f"not on the sandbox allowlist (FLEETWATCH_WRITE_DEVICE_IDS): {', '.join(off)}")

        online = [t for t in record.targets if fleet.devices[t].online]
        if online:
            status = await self._read(reader, "get_recorder_status_for_devices", {"device_ids": online})
            apply_recorder_status(fleet, status)
        until = (now + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        apply_events(fleet, await self._read(reader, "get_current_or_next_cms_events_for_devices", {"until": until}))

        if state_fingerprint(fleet, record.targets) != record.fingerprint:
            raise Refused("a target's state changed since approval (online, recording or next event)")
        if self._tools.is_disruptive(record.tool):
            self._check_rooms(fleet, record, now)

    async def _read(self, reader: EpiphanClient, tool: str, arguments: dict[str, Any]) -> Any:
        try:
            result = await reader.call(tool, arguments)
        except Exception as e:  # noqa: BLE001
            raise Refused(f"couldn't read {tool}: {_clip(e)}") from None
        if not isinstance(result, (dict, list)):
            raise Refused(f"{tool} returned text instead of data")
        return result

    def _check_team(self, raw: Any) -> None:
        if not self._team_id:
            return
        found = [str(raw[k]) for k in _TEAM_KEYS if isinstance(raw, dict) and raw.get(k) not in (None, "")]
        if not found:
            raise Refused(
                "FLEETWATCH_WRITE_TEAM_ID is set, but Epiphan didn't report which team the sandbox sign-in reaches, "
                "so the team can't be checked"
            )
        if any(t != self._team_id for t in found):
            raise Refused("the sandbox sign-in reaches a different team than FLEETWATCH_WRITE_TEAM_ID")

    def _check_rooms(self, fleet: Fleet, record: BoundRecord, now: datetime) -> None:
        lead = timedelta(minutes=self._policy.lead_minutes)
        for target in record.targets:
            device, event = fleet.devices[target], fleet.events.get(target)
            if device.recording:
                raise Refused(f"{target} is recording, and {record.tool} would interrupt it")
            mode = room_state(device, event, now, lead)
            in_window = event is not None and event.start <= now + lead and (event.end is None or now < event.end)
            if mode in (RoomState.LIVE, RoomState.PRE_CLASS) or in_window:
                raise Refused(
                    f"{target} has a {self._policy.event_word} that is on now or starts within "
                    f"{self._policy.lead_minutes} minutes"
                )

    async def _write(self, session: Any, record: BoundRecord, args: dict[str, Any]) -> Outcome:
        """Call the write tool exactly once. No retry: whatever goes wrong after the call starts is `unknown`."""
        self._state.audit("execution_started", {"proposal_id": record.proposal_id, "tool": record.tool})
        try:
            async with asyncio.timeout(self._timeout):
                result = await session.call_tool(record.tool, args)
        except TimeoutError:
            return Outcome("unknown", "Epiphan didn't answer in time; the change may or may not have run")
        except Exception as e:  # noqa: BLE001
            return Outcome("unknown", f"the connection failed ({_clip(type(e).__name__)}); it may or may not have run")
        return _read_result(result)

    # --- outcomes -----------------------------------------------------------------------------------
    def _finish(self, approval_id: int, record: BoundRecord, outcome: Outcome) -> Outcome:
        detail = _clip(outcome.detail)
        errors = {_clip(k): _clip(v) for k, v in outcome.errors.items()}
        outcome = Outcome(outcome.status, detail, errors)
        stored = detail + (f" ({json.dumps(errors, ensure_ascii=False)})" if errors else "")
        try:
            self._state.record_outcome(approval_id, outcome.status, stored)
            self._state.audit(
                "execution",
                {
                    "proposal_id": record.proposal_id,
                    "approval_id": approval_id,
                    "tool": record.tool,
                    "targets": list(record.targets),
                    "outcome": outcome.status,
                    "detail": detail,
                    "errors": errors,
                },
            )
        except Exception as e:  # noqa: BLE001 - the write already happened; say so, don't raise
            log.error("couldn't record the outcome of proposal %s: %s", record.proposal_id, _clip(e))
        return outcome

    def _refused(self, approval_id: int | None, record: Any, reason: str) -> Outcome:
        reason = _clip(reason)
        proposal_id = getattr(record, "proposal_id", None)
        try:
            if approval_id is not None:
                self._state.record_outcome(approval_id, "error", f"refused: {reason}; nothing ran")
            self._state.audit(
                "execution_refused",
                {
                    "proposal_id": proposal_id,
                    "approval_id": approval_id,
                    "tool": str(getattr(record, "tool", "")),
                    "why": reason,
                },
            )
        except Exception as e:  # noqa: BLE001
            log.error("couldn't record a refused change: %s", _clip(e))
        return Outcome("refused", f"Nothing ran: {reason}.")


def _read_result(result: Any) -> Outcome:
    """Epiphan's answer to a write: a map of device ID to error. Empty means ok. Anything else can't be read, so
    it's unknown, never assumed ok."""
    text = redact("".join(getattr(c, "text", "") or "" for c in getattr(result, "content", None) or ()))
    if getattr(result, "is_error", False):
        if _UNAUTHORIZED.search(text):
            return Outcome("unknown", "Epiphan said the sandbox sign-in expired; the change may or may not have run")
        return Outcome("error", f"Epiphan refused the change: {_clip(text)}")
    data = getattr(result, "structured_content", None)
    if data is None:
        try:
            data = json.loads(text)
        except ValueError:
            if _UNAUTHORIZED.search(text):
                return Outcome(
                    "unknown", "Epiphan said the sandbox sign-in expired; the change may or may not have run"
                )
            return Outcome("unknown", "Epiphan's answer couldn't be read; the change may or may not have run")
    data = redact(data)
    if not isinstance(data, dict):
        return Outcome("unknown", "Epiphan's answer couldn't be read; the change may or may not have run")
    if not data:
        return Outcome("ok", "Epiphan reported no errors")
    errors = {str(k): _clip(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)) for k, v in data.items()}
    return Outcome("error", f"Epiphan reported errors on {len(errors)} device(s)", errors)
