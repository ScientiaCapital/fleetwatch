"""The whole heartbeat, end to end, against the saved fleet sample. No network, no sign-in."""

from datetime import timedelta
from pathlib import Path

from fleetwatch.epiphan.replay import ReplayClient
from fleetwatch.heartbeat import tick
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


async def test_replay_heartbeat_posts_once_then_stays_quiet():
    client = ReplayClient(ROOT / "tests/fixtures", load_tool_policy(ROOT / "tool_policy.yaml"))
    state, out = State(), Capture()
    policy = Policy(quiet_start=None, quiet_end=None)
    async with client:
        text = await tick(client, state, policy, out, first_run=True, now=NOW)
        assert text and "Fix first" in text and "FYI:" in text
        assert "offline" in text.lower() and "No picture on" in text
        for banned in ("critical", "urgent", "P1", "—"):
            assert banned not in text
        again = await tick(client, state, policy, out, now=NOW + timedelta(minutes=3))
        assert again is None, "nothing changed, so nothing is posted"
    assert len(out.posts) == 1


async def test_quiet_hours_let_only_fix_first_through():
    client = ReplayClient(ROOT / "tests/fixtures", load_tool_policy(ROOT / "tool_policy.yaml"))
    state, out = State(), Capture()
    from datetime import time

    policy = Policy(quiet_start=time(0, 0), quiet_end=time(23, 59))  # always quiet
    async with client:
        text = await tick(client, state, policy, out, first_run=True, now=NOW)
    assert text and "Fix soon" not in text and "When convenient" not in text and "Fix first" in text
