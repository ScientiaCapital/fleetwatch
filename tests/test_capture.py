"""`digest --capture DIR`: save every tool result a heartbeat read, redacted, as replay files. The wrapper
never calls Epiphan itself: here the inner client is the replay client or a tiny fake."""

import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from fleetwatch import cli
from fleetwatch.epiphan.capture import MANIFEST, CapturingClient
from fleetwatch.epiphan.mcp import ToolNotAllowed
from fleetwatch.epiphan.replay import ReplayClient, resolve_relative_times
from fleetwatch.heartbeat import tick
from fleetwatch.policy import Policy, load_tool_policy
from fleetwatch.redact import redact
from fleetwatch.state import State
from tests.conftest import FIXTURES, NOW
from tests.test_local_hardening import modes, umask_022  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parents[1]
TOOLS = load_tool_policy(ROOT / "tool_policy.yaml")
SAMPLE = (
    "get_devices_in_my_team.json",
    "get_recorder_status_for_devices.json",
    "get_system_status_for_devices.json",
    "get_current_or_next_cms_events_for_devices.json",
)


class Capture:
    def __init__(self):
        self.posts = []

    def post(self, text: str) -> bool:
        self.posts.append(text)
        return True


class Fake:
    """An inner client that answers with whatever it was given. Results are what EpiphanClient would return:
    already redacted (that is the inner client's job, not the wrapper's)."""

    def __init__(self, result: Any = None, error: Exception | None = None):
        self.result, self.error, self.calls, self.tools = result, error, [], TOOLS

    def guard(self, tool: str) -> None:
        if not self.tools.is_read(tool):
            raise ToolNotAllowed(tool)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def call(self, tool: str, arguments: dict | None = None) -> Any:
        self.calls.append(tool)
        if self.error:
            raise self.error
        return self.result


async def _capture_sample(cap: Path) -> str:
    inner = ReplayClient(FIXTURES, TOOLS, now=NOW)
    async with CapturingClient(inner, cap) as client:
        return await tick(client, State(), Policy(quiet_start=None, quiet_end=None), Capture(), first_run=True, now=NOW)


async def test_capture_from_replay_is_byte_identical_to_the_sample(tmp_path):
    cap = tmp_path / "cap"
    await _capture_sample(cap)
    for name in SAMPLE:
        assert (cap / name).read_text() == resolve_relative_times((FIXTURES / name).read_text(), NOW), name
    assert (cap / SAMPLE[0]).read_bytes() == (FIXTURES / SAMPLE[0]).read_bytes()  # no relative times in it


async def test_capture_can_be_replayed(tmp_path):
    cap = tmp_path / "cap"
    first = await _capture_sample(cap)
    async with ReplayClient(cap, TOOLS, now=NOW) as client:
        again = await tick(
            client, State(), Policy(quiet_start=None, quiet_end=None), Capture(), first_run=True, now=NOW
        )
    assert again == first and "Fix first" in again


async def test_result_is_written_as_the_inner_client_returned_it_redacted(tmp_path):
    inner = Fake(redact({"stream_key": "FAKEKEY"}))
    async with CapturingClient(inner, tmp_path / "cap") as client:
        await client.call("get_team_presets")
    text = (tmp_path / "cap" / "get_team_presets.json").read_text()
    assert "FAKEKEY" not in text and "[redacted]" in text


async def test_text_result_round_trips_as_a_json_string(tmp_path):
    cap = tmp_path / "cap"
    async with CapturingClient(Fake("Service unavailable"), cap) as client:
        assert await client.call("get_team_presets") == "Service unavailable"
    assert (cap / "get_team_presets.json").read_text() == '"Service unavailable"\n'
    assert await ReplayClient(cap, TOOLS, now=NOW).call("get_team_presets") == "Service unavailable"


async def test_tool_error_writes_nothing(tmp_path):
    cap = tmp_path / "cap"
    async with CapturingClient(Fake(error=RuntimeError("boom")), cap) as client:
        with pytest.raises(RuntimeError, match="boom"):
            await client.call("get_team_presets")
    assert not (cap / "get_team_presets.json").exists()


@pytest.mark.parametrize("tool", ["batch_reboot", "get_../../x", "some_future_tool", "GET_DEVICES_IN_MY_TEAM"])
async def test_write_and_unknown_tools_never_name_a_file(tmp_path, tool):
    cap, inner = tmp_path / "cap", Fake({"ok": True})
    async with CapturingClient(inner, cap) as client:
        with pytest.raises(ToolNotAllowed):
            await client.call(tool)
    assert inner.calls == [] and [p.name for p in cap.iterdir()] == [MANIFEST]


async def test_capture_dir_and_files_are_private(tmp_path, umask_022, modes):  # noqa: F811
    cap = tmp_path / "private" / "cap"
    await _capture_sample(cap)
    assert stat.S_IMODE(cap.stat().st_mode) == 0o700
    for p in cap.iterdir():
        assert stat.S_IMODE(p.stat().st_mode) == 0o600, p
    assert all(m & 0o077 == 0 for _, _, m in modes), f"something was created wider than 0600: {modes}"


async def test_manifest_lists_tools_and_says_not_for_commit(tmp_path):
    cap = tmp_path / "cap"
    await _capture_sample(cap)
    manifest = json.loads((cap / MANIFEST).read_text())
    assert manifest["tools"] == sorted(n.removesuffix(".json") for n in SAMPLE)
    assert "not for commit" in manifest["note"] and manifest["captured_at"].endswith("Z")


def test_cli_digest_replay_with_capture(tmp_path, monkeypatch, capsys):
    cap = tmp_path / "cap"
    monkeypatch.setattr(sys, "argv", ["fleetwatch", "digest", "--replay", "tests/fixtures", "--capture", str(cap)])
    cli.main()
    assert "Fleet check" in capsys.readouterr().out
    assert sorted(p.name for p in cap.iterdir()) == sorted([*SAMPLE, MANIFEST])
