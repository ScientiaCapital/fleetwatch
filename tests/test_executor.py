"""The v0.2 write executor (docs/design/approved-writes.md, "Flow" steps 4-7, "Binding", "Fence").

Every test talks to a fake Epiphan MCP server that runs in this process and records each call. Nothing here reaches
Epiphan or Anthropic, and nothing signs in: the sandbox "sign-in" is a token file in tmp_path.
"""

import asyncio
import dataclasses
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from fleetwatch.config import Settings
from fleetwatch.epiphan import mcp as mcp_module
from fleetwatch.epiphan.executor import SANDBOX_SLOT, Outcome, WriteExecutor
from fleetwatch.policy import Policy, load_tools
from fleetwatch.proposals import canonical
from fleetwatch.state import State

ROOM = "0a1b2c3d"  # Room 204 Pearl Mini
OTHER = "0e0f1a2b"  # Room 105 Pearl-2, also on the sandbox team
OUTSIDE = "0fffffff"  # not on the sandbox team

STREAM = "3f2a9c1e-5b7d-4e8f-9a0b-1c2d3e4f5a6b"  # a stream endpoint ID, fake
# Narrows the packaged tool_policy.yaml (through the loader) so batch_firmware_update is pending again.
PENDING_FIRMWARE = """
read: [get_devices_in_my_team, get_recorder_status_for_devices, get_current_or_next_cms_events_for_devices]
propose:
  batch_reboot:
    max_targets: 1
    disruptive: true
    schema:
      version: 1
      target: device_ids
      fields:
        device_ids: {type: list, required: true, max_items: 1, items: {type: string, pattern: "^[0-9a-f]{8,32}$"}}
  batch_firmware_update:
    max_targets: 1
    disruptive: true
    schema: pending-live-run
"""


class FakeEdge:
    """An Epiphan MCP server stand-in: read tools answer from fields, write tools record and answer from `reply`."""

    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.recording = False
        self.event: dict[str, Any] | None = None
        self.team_id: str | None = None
        self.reply: Any = {}  # what a write returns: a map, text, an exception, or "sleep"
        self.fail_reads: set[str] = set()
        self.server = self._build()

    @property
    def writes(self) -> list[tuple[str, dict[str, Any]]]:
        return [c for c in self.calls if not c[0].startswith("get_")]

    def _read(self, tool: str, args: dict[str, Any]) -> None:
        self.calls.append((tool, args))
        if tool in self.fail_reads:
            raise ToolError("Epiphan is having trouble")

    async def _write(self, tool: str, args: dict[str, Any]) -> Any:
        self.calls.append((tool, args))
        if self.reply == "sleep":
            await asyncio.sleep(10)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def _build(self) -> MCPServer:
        srv = MCPServer("fake-edge")

        @srv.tool()
        def get_devices_in_my_team() -> dict[str, Any]:
            self._read("get_devices_in_my_team", {})
            out: dict[str, Any] = {
                "devices": [
                    {
                        "Id": ROOM,
                        "Name": "Room 204 Pearl Mini",
                        "Model": "Pearl Mini",
                        "Status": "online",
                        "Channels": [{"id": "1", "name": "Lecture"}],
                    },
                    {"Id": OTHER, "Name": "Room 105 Pearl-2", "Model": "Pearl-2", "Status": "online"},
                ]
            }
            if self.team_id is not None:
                out["team_id"] = self.team_id
            return out

        @srv.tool()
        def get_recorder_status_for_devices(device_ids: list[str]) -> dict[str, Any]:
            self._read("get_recorder_status_for_devices", {"device_ids": device_ids})
            state = "recording" if self.recording else "stopped"
            return {"devices": {ROOM: {"channels": {"1": {"state": state}}}, OTHER: {"channels": {}}}}

        @srv.tool()
        def get_current_or_next_cms_events_for_devices(until: str) -> dict[str, Any]:
            self._read("get_current_or_next_cms_events_for_devices", {"until": until})
            return {"devices": {ROOM: {"event": self.event}, OTHER: {"event": None}}}

        @srv.tool()
        async def batch_reboot(device_ids: list[str]) -> Any:
            return await self._write("batch_reboot", {"device_ids": device_ids})

        @srv.tool()
        async def start_stream_endpoint(stream_id: str, device_id: str, channel_id: str) -> Any:
            args = {"stream_id": stream_id, "device_id": device_id, "channel_id": channel_id}
            return await self._write("start_stream_endpoint", args)

        @srv.tool()
        async def batch_recording(action: str, device_ids: list[str]) -> Any:
            return await self._write("batch_recording", {"action": action, "device_ids": device_ids})

        @srv.tool()
        async def batch_firmware_update(device_ids: list[str]) -> Any:
            return await self._write("batch_firmware_update", {"device_ids": device_ids})

        return srv


@pytest.fixture
def edge(monkeypatch) -> FakeEdge:
    """Every MCP session opened in this test goes to the fake server in this process, never the network."""
    fake = FakeEdge()
    monkeypatch.setattr(mcp_module, "_HttpTransport", lambda url, http: fake.server)
    return fake


def _settings(tmp_path: Path, **kw) -> Settings:
    base: dict[str, Any] = {
        "token_store": "file",  # never the real Keychain from a test
        "token_file": tmp_path / "epiphan-oauth.json",
        "sandbox_token_file": tmp_path / "epiphan-sandbox-oauth.json",
        "epiphan_token": None,
        "write_team_id": "",
        "write_device_ids": f"{ROOM},{OTHER}",  # the fence: v0.2 refuses every change without a team ID or an allowlist
        "epiphan_mcp_url": "https://example.invalid/mcp",
    }
    base.update(kw)
    return Settings(_env_file=None, **base)


def _sign_in_sandbox(s: Settings) -> None:
    s.sandbox_token_file.write_text(
        json.dumps({"tokens": {"access_token": "FAKESANDBOX", "token_type": "Bearer", "expires_in": 3600}})
    )


@pytest.fixture
def tools():
    """The schemas that ship in the package, read through the policy loader."""
    return load_tools()


@pytest.fixture
def sandbox(tmp_path: Path) -> Settings:
    s = _settings(tmp_path)
    _sign_in_sandbox(s)
    return s


def _fp(device: str = ROOM, *, online=True, recording=False, next_event_start=None) -> dict[str, Any]:
    return {device: {"online": online, "recording": recording, "next_event_start": next_event_start}}


def _approved(state: State, tool="batch_reboot", args=None, targets=(ROOM,), fp=None, version=1, slot=SANDBOX_SLOT):
    """Propose, approve and consume: what the approval page does before it hands the record over."""
    args = {"device_ids": [ROOM]} if args is None else args
    pid = state.add_proposal(tool, args, list(targets), fp or _fp(targets[0]), version, slot)
    aid = state.approve(pid, "page-session")
    record = state.consume(aid)
    assert record is not None
    return record, aid


def _executor(settings, state, tools, *, autonomy="propose", timeout_s=5.0) -> WriteExecutor:
    return WriteExecutor(settings, state, policy=Policy(autonomy=autonomy), tools=tools, timeout_s=timeout_s)


async def test_success_runs_exactly_one_write_and_records_ok(edge, sandbox, tools):
    state = State()
    record, aid = _approved(state)
    out = await _executor(sandbox, state, tools).execute(record)
    assert isinstance(out, Outcome) and out.status == "ok", out.detail
    assert edge.writes == [("batch_reboot", {"device_ids": [ROOM]})]
    assert state.outcome(aid) == "ok"
    assert state.recent_audit("execution")[0][1]["outcome"] == "ok"


async def test_the_same_record_never_runs_twice(edge, sandbox, tools):
    state = State()
    record, aid = _approved(state)
    ex = _executor(sandbox, state, tools)
    assert (await ex.execute(record)).status == "ok"
    again = await ex.execute(record)
    assert again.status == "refused"
    assert len(edge.writes) == 1, "one approval, one write"
    assert state.outcome(aid) == "ok", "the first outcome stands"


async def test_a_per_device_error_map_is_an_error_with_the_messages_redacted(edge, sandbox, tools):
    edge.reply = {ROOM: "Device busy; stream key: live_9f8e7d6c"}
    state = State()
    record, aid = _approved(state)
    out = await _executor(sandbox, state, tools).execute(record)
    assert out.status == "error"
    assert set(out.errors) == {ROOM} and "Device busy" in out.errors[ROOM]
    assert "9f8e7d6c" not in json.dumps(out.errors) + out.detail
    assert state.outcome(aid) == "error"
    assert "9f8e7d6c" not in json.dumps(state.recent_audit("execution"), default=str)
    assert len(edge.writes) == 1


@pytest.mark.parametrize(
    "reply", [ToolError("Error: 401 Unauthorized"), "Error: 401 Unauthorized"], ids=["error", "text"]
)
async def test_an_in_band_401_is_unknown_and_never_retried(edge, sandbox, tools, reply):
    edge.reply = reply
    state = State()
    record, aid = _approved(state)
    out = await _executor(sandbox, state, tools).execute(record)
    assert out.status == "unknown"
    assert len(edge.writes) == 1, "no second call, not even after a refresh"
    assert state.outcome(aid) == "unknown"


async def test_a_timeout_is_unknown(edge, sandbox, tools):
    edge.reply = "sleep"
    state = State()
    record, aid = _approved(state)
    out = await _executor(sandbox, state, tools, timeout_s=0.5).execute(record)
    assert out.status == "unknown"
    assert len(edge.writes) == 1
    assert state.outcome(aid) == "unknown"


async def test_a_result_that_isnt_a_map_is_unknown(edge, sandbox, tools):
    edge.reply = "Done, probably"
    state = State()
    record, aid = _approved(state)
    assert (await _executor(sandbox, state, tools).execute(record)).status == "unknown"
    assert state.outcome(aid) == "unknown"


async def test_a_target_outside_the_sandbox_team_is_refused_without_a_write(edge, sandbox, tools):
    state = State()
    record, aid = _approved(state, args={"device_ids": [OUTSIDE]}, targets=(OUTSIDE,))
    out = await _executor(sandbox, state, tools).execute(record)
    assert out.status == "refused" and "sandbox" in out.detail
    assert edge.writes == []
    assert state.outcome(aid) == "error"
    assert state.recent_audit("execution_refused")


async def test_a_channel_id_counts_as_its_master_device(edge, sandbox, tools):
    state = State()
    args = {"action": "start", "device_ids": [f"{ROOM}-1"]}
    record, _ = _approved(state, tool="batch_recording", args=args, targets=(ROOM,))
    assert (await _executor(sandbox, state, tools).execute(record)).status == "ok"
    assert edge.writes == [("batch_recording", args)]


async def test_a_channel_on_a_device_that_wasnt_approved_is_refused(edge, sandbox, tools):
    state = State()
    args = {"action": "start", "device_ids": [f"{OTHER}-1"]}
    record, _ = _approved(state, tool="batch_recording", args=args, targets=(ROOM,))
    out = await _executor(sandbox, state, tools).execute(record)
    assert out.status == "refused" and edge.calls == []


async def test_a_channel_whose_master_isnt_on_the_sandbox_team_is_refused(edge, sandbox, tools):
    state = State()
    args = {"action": "start", "device_ids": [f"{OUTSIDE}-1"]}
    record, _ = _approved(state, tool="batch_recording", args=args, targets=(OUTSIDE,))
    out = await _executor(sandbox, state, tools).execute(record)
    assert out.status == "refused" and "sandbox" in out.detail
    assert edge.writes == []


async def test_a_changed_fingerprint_is_refused(edge, sandbox, tools):
    edge.recording = True  # it was idle when the card was approved
    state = State()
    args = {"stream_id": STREAM, "device_id": ROOM, "channel_id": "1"}
    record, aid = _approved(state, tool="start_stream_endpoint", args=args)
    out = await _executor(sandbox, state, tools).execute(record)
    assert out.status == "refused" and "changed" in out.detail
    assert edge.writes == []
    assert state.outcome(aid) == "error"


async def test_a_disruptive_change_while_recording_is_refused(edge, sandbox, tools):
    edge.recording = True
    state = State()
    record, _ = _approved(state, fp=_fp(recording=True))  # same state as approved, so only the room rule says no
    out = await _executor(sandbox, state, tools).execute(record)
    assert out.status == "refused" and "recording" in out.detail
    assert edge.writes == []


async def test_a_disruptive_change_inside_the_readiness_window_is_refused(edge, sandbox, tools):
    start = (datetime.now(UTC) + timedelta(minutes=10)).replace(microsecond=0)
    edge.event = {"id": "e1", "title": "Weekly review", "start": start.isoformat(), "end": None}
    state = State()
    record, _ = _approved(state, fp=_fp(next_event_start=start.isoformat()))
    out = await _executor(sandbox, state, tools).execute(record)
    assert out.status == "refused" and "starts within" in out.detail
    assert edge.writes == []


async def test_a_disruptive_change_well_before_the_next_event_runs(edge, sandbox, tools):
    start = (datetime.now(UTC) + timedelta(hours=5)).replace(microsecond=0)
    edge.event = {"id": "e1", "title": "Weekly review", "start": start.isoformat(), "end": None}
    state = State()
    record, _ = _approved(state, fp=_fp(next_event_start=start.isoformat()))
    assert (await _executor(sandbox, state, tools).execute(record)).status == "ok"


async def test_a_failed_read_fails_closed(edge, sandbox, tools):
    edge.fail_reads = {"get_recorder_status_for_devices"}
    state = State()
    record, _ = _approved(state)
    out = await _executor(sandbox, state, tools).execute(record)
    assert out.status == "refused" and edge.writes == []


async def test_autonomy_observe_is_refused(edge, sandbox, tools):
    state = State()
    record, aid = _approved(state)
    out = await _executor(sandbox, state, tools, autonomy="observe").execute(record)
    assert out.status == "refused" and "observe" in out.detail
    assert edge.calls == [], "refused before any session opens"
    assert state.outcome(aid) == "error"


async def test_a_pending_schema_is_refused(edge, sandbox, tmp_path):
    narrow = tmp_path / "tool_policy.yaml"
    narrow.write_text(PENDING_FIRMWARE)
    pending = load_tools(narrow)
    assert pending.propose["batch_firmware_update"].pending
    state = State()
    record, _ = _approved(state, tool="batch_firmware_update")
    out = await _executor(sandbox, state, pending).execute(record)
    assert out.status == "refused" and edge.calls == []


async def test_a_tool_that_isnt_proposable_is_refused(edge, sandbox, tools):
    state = State()
    record, _ = _approved(state, tool="get_devices_in_my_team", args={})
    out = await _executor(sandbox, state, tools).execute(record)
    assert out.status == "refused" and edge.calls == []


async def test_arguments_that_fail_the_schema_are_refused(edge, sandbox, tools):
    state = State()
    record, _ = _approved(state, args={"device_ids": [ROOM], "force": True})
    out = await _executor(sandbox, state, tools).execute(record)
    assert out.status == "refused" and edge.calls == []


async def test_targets_that_dont_match_the_arguments_are_refused(edge, sandbox, tools):
    state = State()
    record, _ = _approved(state, args={"device_ids": [OTHER]}, targets=(ROOM,))
    out = await _executor(sandbox, state, tools).execute(record)
    assert out.status == "refused" and edge.calls == []


async def test_a_stale_schema_version_is_refused(edge, sandbox, tools):
    state = State()
    record, _ = _approved(state, version=2)
    out = await _executor(sandbox, state, tools).execute(record)
    assert out.status == "refused" and edge.calls == []


async def test_a_record_for_another_slot_is_refused(edge, sandbox, tools):
    state = State()
    record, _ = _approved(state, slot="normal")
    out = await _executor(sandbox, state, tools).execute(record)
    assert out.status == "refused" and edge.calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"arguments": canonical({"device_ids": [OTHER]})},
        {"targets": (OTHER,)},
        {"tool": "start_stream_endpoint"},
        {"fingerprint": _fp(recording=True)},
    ],
    ids=["arguments", "targets", "tool", "fingerprint"],
)
async def test_a_record_tampered_after_consume_is_refused(edge, sandbox, tools, change):
    state = State()
    record, aid = _approved(state)
    forged = dataclasses.replace(record, **change)
    out = await _executor(sandbox, state, tools).execute(forged)
    assert out.status == "refused" and edge.calls == []
    assert state.outcome(aid) == "unknown", "the real approval is untouched: consumed, nothing recorded"


async def test_a_record_that_was_never_consumed_is_refused(edge, sandbox, tools):
    state = State()
    pid = state.add_proposal("batch_reboot", {"device_ids": [ROOM]}, [ROOM], _fp(), 1, SANDBOX_SLOT)
    state.approve(pid, "page-session")
    record = state.bound_record(pid)  # what a card shows; never something to run
    out = await _executor(sandbox, state, tools).execute(record)
    assert out.status == "refused" and edge.calls == []


async def test_no_fence_at_all_is_refused_before_any_read(edge, tmp_path, tools):
    s = _settings(tmp_path, write_device_ids="", write_team_id="")
    _sign_in_sandbox(s)
    state = State()
    record, aid = _approved(state)
    out = await _executor(s, state, tools).execute(record)
    assert out.status == "refused" and "FLEETWATCH_WRITE_DEVICE_IDS" in out.detail
    assert edge.calls == []
    assert state.outcome(aid) == "error"


async def test_a_target_on_the_team_but_off_the_allowlist_is_refused(edge, tmp_path, tools):
    s = _settings(tmp_path, write_device_ids=OTHER)
    _sign_in_sandbox(s)
    state = State()
    record, _ = _approved(state)  # ROOM is on the team's device list, but not on the allowlist
    out = await _executor(s, state, tools).execute(record)
    assert out.status == "refused" and "allowlist" in out.detail
    assert edge.writes == []


async def test_a_team_id_alone_is_a_fence(edge, tmp_path, tools):
    s = _settings(tmp_path, write_device_ids="", write_team_id="team-sandbox")
    _sign_in_sandbox(s)
    edge.team_id = "team-sandbox"
    state = State()
    record, _ = _approved(state)
    assert (await _executor(s, state, tools).execute(record)).status == "ok"


async def test_the_allowlist_ignores_case_spaces_and_blank_entries(edge, tmp_path, tools):
    s = _settings(tmp_path, write_device_ids=f" , {ROOM.upper()} ,, ")
    _sign_in_sandbox(s)
    state = State()
    record, _ = _approved(state)
    assert (await _executor(s, state, tools).execute(record)).status == "ok"


async def test_a_team_id_mismatch_is_refused(edge, tmp_path, tools):
    s = _settings(tmp_path, write_team_id="team-sandbox")
    _sign_in_sandbox(s)
    edge.team_id = "team-production"
    state = State()
    record, _ = _approved(state)
    out = await _executor(s, state, tools).execute(record)
    assert out.status == "refused" and "team" in out.detail
    assert edge.writes == []


async def test_a_team_id_that_cant_be_read_is_refused(edge, tmp_path, tools):
    s = _settings(tmp_path, write_team_id="team-sandbox")
    _sign_in_sandbox(s)
    state = State()
    record, _ = _approved(state)
    out = await _executor(s, state, tools).execute(record)
    assert out.status == "refused" and "FLEETWATCH_WRITE_TEAM_ID" in out.detail
    assert edge.writes == []


async def test_a_matching_team_id_runs(edge, tmp_path, tools):
    s = _settings(tmp_path, write_team_id="team-sandbox")
    _sign_in_sandbox(s)
    edge.team_id = "team-sandbox"
    state = State()
    record, _ = _approved(state)
    assert (await _executor(s, state, tools).execute(record)).status == "ok"


async def test_no_sandbox_sign_in_is_refused(edge, tmp_path, tools):
    s = _settings(tmp_path)
    state = State()
    record, aid = _approved(state)
    out = await _executor(s, state, tools).execute(record)
    assert out.status == "refused" and "sandbox sign-in" in out.detail.lower()
    assert edge.calls == []
    assert state.outcome(aid) == "error"


async def test_the_normal_sign_in_never_stands_in_for_the_sandbox(edge, tmp_path, tools):
    """A static token and a normal token file are both the normal slot: neither lets a change run."""
    s = _settings(tmp_path, epiphan_token="FAKESTATIC")
    s.token_file.write_text(json.dumps({"tokens": {"access_token": "FAKENORMAL", "token_type": "Bearer"}}))
    state = State()
    record, _ = _approved(state)
    out = await _executor(s, state, tools).execute(record)
    assert out.status == "refused" and edge.calls == []


def test_the_sandbox_slot_cant_be_the_normal_token_file(tmp_path, tools):
    s = _settings(tmp_path, sandbox_token_file=tmp_path / "epiphan-oauth.json")
    with pytest.raises(ValueError, match="sandbox"):
        _executor(s, State(), tools)


async def test_an_unexpected_failure_never_raises_with_a_secret(edge, sandbox, tools, monkeypatch):
    state = State()
    record, _ = _approved(state)
    ex = _executor(sandbox, state, tools)

    async def boom(*a, **k):
        raise RuntimeError("password=hunter2 went wrong")

    monkeypatch.setattr(ex, "_reads", boom)
    out = await ex.execute(record)
    assert out.status == "refused" and "hunter2" not in out.detail
    assert edge.writes == []
    assert "hunter2" not in json.dumps(state.recent_audit("execution_refused"), default=str)
