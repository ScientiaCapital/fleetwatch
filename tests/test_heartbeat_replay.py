"""The whole heartbeat, end to end, against the saved fleet sample. No network, no sign-in."""

from datetime import timedelta
from pathlib import Path

import pytest

from fleetwatch.epiphan.replay import ReplayClient
from fleetwatch.heartbeat import FailedRead, snapshot, tick
from fleetwatch.policy import Policy, load_tool_policy
from fleetwatch.state import State
from tests.conftest import NOW

ROOT = Path(__file__).resolve().parents[1]


class Capture:
    def __init__(self):
        self.posts = []

    def post(self, text: str) -> bool:
        self.posts.append(text)
        return True


def replay_client():
    return ReplayClient(ROOT / "tests/fixtures", load_tool_policy(ROOT / "tool_policy.yaml"), now=NOW)


async def test_replay_heartbeat_posts_once_then_stays_quiet():
    client = replay_client()
    state, out = State(), Capture()
    policy = Policy(quiet_start=None, quiet_end=None)
    async with client:
        text = await tick(client, state, policy, out, first_run=True, now=NOW)
        assert text and "Fix first" in text and "FYI:" in text
        assert "offline" in text.lower() and "No picture on" in text
        for banned in ("critical", "urgent", "P1", "—"):
            assert banned not in text
        first_posts = len(out.posts)
        again = await tick(client, state, policy, out, now=NOW + timedelta(minutes=3))
        assert again is None, "nothing changed, so nothing is posted"
    assert len(out.posts) == first_posts, "readiness checks are posted once per class too"


async def test_quiet_hours_let_only_fix_first_through():
    client = replay_client()
    state, out = State(), Capture()
    from datetime import time

    policy = Policy(quiet_start=time(0, 0), quiet_end=time(23, 59))  # always quiet
    async with client:
        text = await tick(client, state, policy, out, first_run=True, now=NOW)
    assert text and "Fix soon" not in text and "When convenient" not in text and "Fix first" in text


async def test_demo_sample_covers_every_read_and_both_readiness_verdicts(caplog):
    """The sample is the offline demo: every tool the heartbeat reads has a saved result, and it shows the
    whole product, including a Ready and a Not ready check before class."""
    client, state, out = replay_client(), State(), Capture()
    async with client:
        text = await tick(client, state, Policy(quiet_start=None, quiet_end=None), out, first_run=True, now=NOW)
    assert not [r for r in caplog.records if "failed" in r.getMessage()], "no tool should be missing from the sample"
    assert "working hard" in text and "running warm" in text and "restarted about 12 minutes ago" in text
    readiness = [p for p in out.posts if " at " in p.splitlines()[0] and p.startswith("*")]
    assert any(p.splitlines()[0].endswith("*Not ready*") and "No picture on Camera 2" in p for p in readiness)
    assert any(p.splitlines()[0].endswith("*Ready*") for p in readiness)
    assert len(readiness) == 2, "the class three hours out is not checked yet"


async def test_replay_resolves_relative_times(tmp_path):
    (tmp_path / "get_team_presets.json").write_text('{"a": "{{now+25m}}", "b": "{{now-2h}}", "c": "{{now}}"}')
    client = ReplayClient(tmp_path, load_tool_policy(ROOT / "tool_policy.yaml"), now=NOW)
    got = await client.call("get_team_presets")
    assert got == {"a": "2026-10-07T15:25:00Z", "b": "2026-10-07T13:00:00Z", "c": "2026-10-07T15:00:00Z"}


async def test_calm_sample_is_all_clear_with_one_ready_check():
    """The calm sample is for a screen in a quiet room: nothing to fix, one event coming up and it is Ready."""
    client = ReplayClient(ROOT / "tests/fixtures/calm", load_tool_policy(ROOT / "tool_policy.yaml"), now=NOW)
    state, out = State(), Capture()
    async with client:
        text = await tick(client, state, Policy(quiet_start=None, quiet_end=None), out, first_run=True, now=NOW)
    assert text and "All clear" in text
    for word in ("Fix first", "Fix soon", "When convenient", "FYI:"):
        assert word not in text
    readiness = [p for p in out.posts if " at " in p.splitlines()[0] and p.startswith("*")]
    assert len(readiness) == 1 and readiness[0].splitlines()[0].endswith("*Ready*")


async def test_calm_then_a_room_goes_offline_posts_now_not_ready(tmp_path):
    """Two heartbeats: the calm sample, then a copy where the Pearl with the next event is unplugged. The room
    said Ready, so it now says Now not ready, once."""
    import json
    import shutil

    down = tmp_path / "down"
    shutil.copytree(ROOT / "tests/fixtures/calm", down)
    devices = json.loads((down / "get_devices_in_my_team.json").read_text())
    next(d for d in devices["devices"] if d["Id"] == "1f000000013")["Status"] = "offline"
    (down / "get_devices_in_my_team.json").write_text(json.dumps(devices))

    tools, policy = load_tool_policy(ROOT / "tool_policy.yaml"), Policy(quiet_start=None, quiet_end=None)
    state, out = State(), Capture()
    async with ReplayClient(ROOT / "tests/fixtures/calm", tools, now=NOW) as client:
        await tick(client, state, policy, out, first_run=True, now=NOW)
    async with ReplayClient(down, tools, now=NOW) as client:
        text = await tick(client, state, policy, out, now=NOW + timedelta(minutes=3))
        await tick(client, state, policy, out, now=NOW + timedelta(minutes=6))
    assert text and "Fix first" in text and "Main Stage Pearl Nano is offline" in text
    readiness = [p for p in out.posts if " at " in p.splitlines()[0] and p.startswith("*")]
    assert [p.splitlines()[0].rsplit(": ", 1)[1] for p in readiness] == ["*Ready*", "*Now not ready* (was Ready)"]


class _Failing(ReplayClient):
    """The replay sample, except one named read fails."""

    def __init__(self, fail: str):
        super().__init__(ROOT / "tests/fixtures", load_tool_policy(ROOT / "tool_policy.yaml"), now=NOW)
        self.fail = fail

    async def call(self, tool, arguments=None):
        if tool == self.fail:
            raise RuntimeError("the read failed")
        return await super().call(tool, arguments)


@pytest.mark.parametrize("tool", ["get_recorder_status_for_devices", "get_current_or_next_cms_events_for_devices"])
async def test_snapshot_forgives_a_failed_read_by_default(tool):
    fleet = await snapshot(_Failing(tool), NOW)
    assert fleet.devices, "the heartbeat keeps working from what it could read"


@pytest.mark.parametrize(
    "tool",
    ["get_recorder_status_for_devices", "get_system_status_for_devices", "get_current_or_next_cms_events_for_devices"],
)
async def test_strict_snapshot_raises_on_any_failed_read(tool):
    with pytest.raises(FailedRead):
        await snapshot(_Failing(tool), NOW, strict=True)


async def test_strict_snapshot_matches_the_default_when_every_read_works():
    plain = await snapshot(replay_client(), NOW)
    strict = await snapshot(replay_client(), NOW, strict=True)
    assert strict == plain
