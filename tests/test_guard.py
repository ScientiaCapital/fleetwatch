"""The agent can only ever call read tools. The guard lives in the client, before anything reaches the network."""

import json
from pathlib import Path

import pytest

from fleetwatch.epiphan.mcp import EpiphanClient, ToolNotAllowed
from fleetwatch.policy import KNOWN_WRITE_TOOLS, load_policy, load_tool_policy

ROOT = Path(__file__).resolve().parents[1]


def test_tool_policy_file_is_consistent():
    tp = load_tool_policy(ROOT / "tool_policy.yaml")
    assert "get_devices_in_my_team" in tp.read and "batch_reboot" in tp.write
    assert tp.disruptive <= tp.write and not (tp.read & tp.write)


@pytest.mark.parametrize(
    "tool", ["batch_recording", "batch_reboot", "apply_team_preset", "some_future_tool", "create_cms_event"]
)
def test_writes_and_unknown_tools_refused_before_network(tool):
    client = EpiphanClient("https://example.invalid/mcp", load_tool_policy(ROOT / "tool_policy.yaml"), static_token="x")
    with pytest.raises(ToolNotAllowed):
        client.guard(tool)


def test_reads_allowed():
    client = EpiphanClient("https://example.invalid/mcp", load_tool_policy(ROOT / "tool_policy.yaml"), static_token="x")
    client.guard("get_devices_in_my_team")
    client.guard("kb_search")


def test_policy_forces_observe_and_dry_run(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("autonomy: observe\ndry_run: false\n")
    assert load_policy(p).dry_run is True
    p.write_text("autonomy: auto\n")
    with pytest.raises(ValueError):
        load_policy(p)


def test_shipped_policy_loads():
    pol = load_policy(ROOT / "policy.yaml")
    assert pol.autonomy == "observe" and pol.dry_run and pol.heartbeat_seconds >= 60


def test_guard_refuses_every_write_tool_with_autonomy_propose(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("autonomy: propose\n")
    assert load_policy(p).autonomy == "propose"
    tools = load_tool_policy(ROOT / "tool_policy.yaml")
    client = EpiphanClient("https://example.invalid/mcp", tools, static_token="x")
    for tool in sorted(KNOWN_WRITE_TOOLS | tools.write | set(tools.propose)):
        with pytest.raises(ToolNotAllowed):
            client.guard(tool)


def test_propose_tools_never_reach_the_read_list():
    tools = load_tool_policy(ROOT / "tool_policy.yaml")
    assert not set(tools.propose) & tools.read
    assert not tools.read & KNOWN_WRITE_TOOLS


SRC = ROOT / "src" / "fleetwatch"
# The only two places an MCP tool may be called: the read-only client (behind guard()) and the v0.2 write executor.
CALLERS = {SRC / "epiphan" / "mcp.py", SRC / "epiphan" / "executor.py"}


def test_call_tool_is_used_only_by_the_two_client_classes():
    """A new `call_tool` anywhere else would be a path to Epiphan that skips both guard() and the executor's checks."""
    found = sorted(
        str(path.relative_to(ROOT))
        for path in SRC.rglob("*.py")
        if path not in CALLERS and "call_tool" in path.read_text(encoding="utf-8")
    )
    assert found == [], f"call_tool referenced outside mcp.py and executor.py: {found}"
    assert all("call_tool" in p.read_text(encoding="utf-8") for p in CALLERS)


class _RecordingSDK:
    def __init__(self):
        self.calls = []

    async def call_tool(self, tool, arguments):
        self.calls.append(tool)
        raise AssertionError(f"{tool} reached the session")


async def test_epiphan_client_refuses_every_write_even_with_propose_and_a_sandbox_sign_in(tmp_path):
    """autonomy: propose and a sandbox sign-in change nothing for EpiphanClient: writes go only through the
    executor, never through call()."""
    from fleetwatch.config import Settings
    from fleetwatch.epiphan.executor import sandbox_store

    p = tmp_path / "policy.yaml"
    p.write_text("autonomy: propose\n")
    assert load_policy(p).proposes
    s = Settings(
        _env_file=None,
        token_store="file",
        token_file=tmp_path / "epiphan-oauth.json",
        sandbox_token_file=tmp_path / "epiphan-sandbox-oauth.json",
    )
    s.sandbox_token_file.write_text(json.dumps({"tokens": {"access_token": "FAKESANDBOX", "token_type": "Bearer"}}))
    store = sandbox_store(s)
    assert store.has_tokens()
    tools = load_tool_policy(ROOT / "tool_policy.yaml")
    client = EpiphanClient("https://example.invalid/mcp", tools, storage=store)
    client._client = sdk = _RecordingSDK()
    for tool in sorted(KNOWN_WRITE_TOOLS | tools.write | set(tools.propose)):
        with pytest.raises(ToolNotAllowed):
            await client.call(tool, {"device_ids": ["0a1b2c3d"]})
    assert sdk.calls == []
