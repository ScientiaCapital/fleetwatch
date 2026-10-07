"""The agent can only ever call read tools. The guard lives in the client, before anything reaches the network."""

from pathlib import Path

import pytest

from proav_agent.epiphan.mcp import EpiphanClient, ToolNotAllowed
from proav_agent.policy import load_policy, load_tool_policy

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
