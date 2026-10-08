"""`fleetwatch approve --serve`: when it refuses to start, and the replay variant end to end.

Nothing here signs in or calls Epiphan or Anthropic. The sandbox "sign-in" is a token file in tmp_path, and replay
reads come from tests/fixtures.
"""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest

from fleetwatch import cli
from fleetwatch.approve_page import RecordingExecutor
from fleetwatch.config import Settings
from fleetwatch.epiphan import executor as executor_module
from fleetwatch.proposals import canonical, parse_canonical

FIXTURES = Path(__file__).parent / "fixtures"
REPO_POLICY = Path(__file__).parent.parent / "policy.yaml"


def _settings(tmp_path: Path, autonomy: str = "observe", **kw) -> Settings:
    policy = tmp_path / "policy.yaml"
    policy.write_text(re.sub(r"(?m)^autonomy:.*$", f"autonomy: {autonomy}", REPO_POLICY.read_text()))
    base: dict[str, Any] = {
        "token_store": "file",  # never the real Keychain from a test
        "token_file": tmp_path / "epiphan-oauth.json",
        "sandbox_token_file": tmp_path / "epiphan-sandbox-oauth.json",
        "epiphan_token": None,
        "state_db": tmp_path / "state.db",
        "policy_file": policy,
    }
    base.update(kw)
    return Settings(**base)


def _sign_in_sandbox(s: Settings) -> None:
    s.sandbox_token_file.write_text(
        json.dumps({"tokens": {"access_token": "FAKESANDBOX", "token_type": "Bearer", "expires_in": 3600}})
    )


@pytest.fixture
def no_real_executor(monkeypatch):
    """Any attempt to build the real WriteExecutor fails the test."""

    def boom(*_a, **_k):
        raise AssertionError("replay mode built the real WriteExecutor")

    monkeypatch.setattr(executor_module.WriteExecutor, "__init__", boom)


def test_refuses_to_start_in_observe_mode(tmp_path, capsys):
    s = _settings(tmp_path, "observe")
    _sign_in_sandbox(s)
    with pytest.raises(SystemExit) as e:
        cli._approve_page(s, None)
    assert e.value.code != 0
    assert "autonomy: propose" in capsys.readouterr().out


def test_refuses_to_start_without_a_write_fence(tmp_path, capsys):
    s = _settings(tmp_path, "propose")  # signed in below, but no team ID and no device allowlist
    _sign_in_sandbox(s)
    with pytest.raises(SystemExit) as e:
        cli._approve_page(s, None)
    assert e.value.code != 0
    assert "FLEETWATCH_WRITE_DEVICE_IDS" in capsys.readouterr().out


def test_sandbox_login_overlapping_the_normal_sign_in_is_refused_and_forgotten(tmp_path, capsys):
    from datetime import UTC, datetime

    from fleetwatch.epiphan.parse import parse_devices
    from fleetwatch.state import State

    s = _settings(tmp_path, "propose")
    _sign_in_sandbox(s)
    both = {"devices": [{"Id": "0a1b2c3d", "Name": "Room 204 Pearl Mini", "Status": "online"}]}
    State(s.state_db).record_devices(parse_devices(both, datetime.now(UTC)), datetime.now(UTC))
    with pytest.raises(SystemExit) as e:
        cli._refuse_overlapping_sandbox(s, both)
    assert e.value.code != 0
    assert "same team" in capsys.readouterr().out
    assert not s.sandbox_token_file.exists(), "a refused sandbox sign-in is forgotten"


def test_sandbox_login_that_lists_no_devices_is_refused_and_forgotten(tmp_path, capsys):
    s = _settings(tmp_path, "propose")
    _sign_in_sandbox(s)
    for odd in ({"devices": []}, "Epiphan is having trouble", {"error": "nope"}):
        _sign_in_sandbox(s)
        with pytest.raises(SystemExit) as e:
            cli._refuse_overlapping_sandbox(s, odd)
        assert e.value.code != 0
        assert not s.sandbox_token_file.exists()
    assert "no devices" in capsys.readouterr().out


def test_sandbox_login_with_no_normal_history_is_kept_with_a_warning(tmp_path, capsys):
    s = _settings(tmp_path, "propose")
    _sign_in_sandbox(s)
    cli._refuse_overlapping_sandbox(s, {"devices": [{"Id": "0e0f1a2b", "Name": "Lab Pearl-2", "Status": "online"}]})
    assert s.sandbox_token_file.exists()
    assert "can't be compared" in capsys.readouterr().out


def test_a_sandbox_sign_in_that_cant_be_cleared_says_so_instead_of_a_traceback(tmp_path, capsys, monkeypatch):
    from datetime import UTC, datetime

    from fleetwatch.epiphan.parse import parse_devices
    from fleetwatch.state import State

    s = _settings(tmp_path, "propose")
    _sign_in_sandbox(s)
    both = {"devices": [{"Id": "0a1b2c3d", "Name": "Room 204 Pearl Mini", "Status": "online"}]}
    State(s.state_db).record_devices(parse_devices(both, datetime.now(UTC)), datetime.now(UTC))

    class Stuck:
        def clear(self):
            raise OSError("keychain locked")

    monkeypatch.setattr(cli, "_sandbox_store", lambda _s: Stuck())
    with pytest.raises(SystemExit) as e:
        cli._refuse_overlapping_sandbox(s, both)
    out = capsys.readouterr().out
    assert e.value.code != 0 and "fleetwatch logout --sandbox" in out and "forgotten" not in out


def test_sandbox_login_with_different_devices_is_kept(tmp_path):
    from datetime import UTC, datetime

    from fleetwatch.epiphan.parse import parse_devices
    from fleetwatch.state import State

    s = _settings(tmp_path, "propose")
    _sign_in_sandbox(s)
    normal = {"devices": [{"Id": "0a1b2c3d", "Name": "Room 204 Pearl Mini", "Status": "online"}]}
    State(s.state_db).record_devices(parse_devices(normal, datetime.now(UTC)), datetime.now(UTC))
    cli._refuse_overlapping_sandbox(s, {"devices": [{"Id": "0e0f1a2b", "Name": "Lab Pearl-2", "Status": "online"}]})
    assert s.sandbox_token_file.exists()


def test_refuses_to_start_without_a_sandbox_sign_in(tmp_path, capsys):
    s = _settings(tmp_path, "propose")
    with pytest.raises(SystemExit) as e:
        cli._approve_page(s, None)
    assert e.value.code != 0
    assert "fleetwatch login --sandbox" in capsys.readouterr().out


def test_starts_in_propose_mode_with_a_sandbox_sign_in_and_a_fence(tmp_path):
    s = _settings(tmp_path, "propose", write_device_ids="0a1b2c3d")
    _sign_in_sandbox(s)
    page = cli._approve_page(s, None)
    assert isinstance(page.executor, executor_module.WriteExecutor)
    assert page.port == s.approve_port


def test_approve_needs_serve(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["fleetwatch", "approve"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 2
    assert "--serve" in capsys.readouterr().err


def test_approve_port_default_differs_from_the_ask_page():
    s = Settings(_env_file=None)
    assert s.approve_port != s.ask_port and s.approve_port != s.oauth_callback_port


def test_replay_never_builds_the_real_executor_and_seeds_one_sample(tmp_path, no_real_executor):
    page = cli._approve_page(_settings(tmp_path, "observe"), str(FIXTURES))
    assert isinstance(page.executor, RecordingExecutor)
    pending = page.state.pending_proposals()
    assert len(pending) == 1 and pending[0][1].slot == "replay"


def test_replay_end_to_end_records_one_call_with_the_exact_canonical_args(tmp_path, no_real_executor):
    page = cli._approve_page(_settings(tmp_path, "observe"), str(FIXTURES))
    ((pid, record, _reason),) = page.state.pending_proposals()
    sid = page.new_session()
    card = page.render_card_page(sid, "en")
    assert record.targets[0] in card and 'action="/approve"' in card

    # The card, then Approve, then (batch_reboot is disruptive) the confirm step.
    code, confirm = page.handle_post(
        "/approve", {"proposal": [str(pid)], "token": [page.token("card", sid, pid)]}, sid, "en"
    )
    assert code == 200 and 'action="/confirm"' in confirm and page.executor.calls == []
    code, done = page.handle_post(
        "/confirm", {"proposal": [str(pid)], "token": [page.token("confirm", sid, pid)]}, sid, "en"
    )
    assert code == 200 and "Nothing was sent" in done
    assert page.executor.calls == [("batch_reboot", canonical({"device_ids": [record.targets[0]]}))]
    assert parse_canonical(page.executor.calls[0][1]) == {"device_ids": [record.targets[0]]}

    code, _again = page.handle_post(
        "/confirm", {"proposal": [str(pid)], "token": [page.token("confirm", sid, pid)]}, sid, "en"
    )
    assert code == 409 and len(page.executor.calls) == 1


def test_replay_proposals_are_bound_to_a_slot_the_real_executor_refuses(tmp_path, no_real_executor):
    page = cli._approve_page(_settings(tmp_path, "observe"), str(FIXTURES))
    ((_pid, record, _),) = page.state.pending_proposals()
    assert record.slot != executor_module.SANDBOX_SLOT


def test_replay_reads_go_through_the_replay_client_only(tmp_path, no_real_executor):
    page = cli._approve_page(_settings(tmp_path, "observe"), str(FIXTURES))
    fleet = asyncio.run(page.read_fleet())
    assert fleet.devices


# --- the chat box, wired to the assistant ------------------------------------------------------------------------
def _post(page, path, sid, pid, purpose):
    return page.handle_post(path, {"proposal": [str(pid)], "token": [page.token(purpose, sid, pid)]}, sid, "en")


def test_replay_chat_proposal_becomes_a_card_and_one_approval_records_one_write(tmp_path, no_real_executor):
    """A mocked model proposes a change through the chat box; it appears as a card; Approve (a start isn't
    disruptive, so there's no confirm step) records exactly one write; approving the same proposal again is refused."""
    from tests.test_assistant import COURTROOM, COURTROOM_CH1, FakeAnthropic, propose, reply, text, tool_use

    fake = FakeAnthropic(reply(tool_use("propose_change", propose())), reply(text("I proposed it.")))
    s = _settings(tmp_path, "observe", anthropic_api_key="sk-ant-test")  # a replay still lets the model propose
    page = cli._approve_page(s, str(FIXTURES), model_client=fake)
    assert isinstance(page.executor, RecordingExecutor) and page.ask_fn is not None
    sid = page.new_session()
    ((sample, _r, _w),) = page.state.pending_proposals()
    assert page.state.deny(sample, sid)  # the demo sample; leave only what the chat proposes

    q = {"q": ["start recording in Courtroom"], "token": [page.token("ask", sid, 0)]}
    code, answered = page.handle_post("/ask", q, sid, "en")
    assert code == 200 and "I proposed it." in answered
    ((pid, record, _why),) = page.state.pending_proposals()
    assert record.slot == "replay" and record.targets == (COURTROOM,)
    card = page.render_card_page(sid, "en")
    assert COURTROOM in card and 'action="/approve"' in card

    code, done = _post(page, "/approve", sid, pid, "card")  # a start isn't disruptive: no confirm step
    assert code == 200 and "Nothing was sent" in done
    assert page.executor.calls == [("batch_recording", canonical({"action": "start", "device_ids": [COURTROOM_CH1]}))]

    code, _ = _post(page, "/approve", sid, pid, "card")
    assert code == 409 and len(page.executor.calls) == 1


@pytest.mark.parametrize(("no_ai", "key"), [(False, None), (True, "sk-ant-test")])
def test_chat_without_a_key_or_with_no_ai_uses_the_keyword_answer_and_says_so(tmp_path, no_ai, key, no_real_executor):
    s = _settings(tmp_path, "observe", anthropic_api_key=key)
    page = cli._approve_page(s, str(FIXTURES), no_ai=no_ai)
    answer = page.ask_fn("what needs attention")
    assert "assistant is off" in answer and "quick answer" in answer
    assert len(page.state.pending_proposals()) == 1  # only the replay sample: the keyword answer can't propose


def test_every_replay_client_the_page_builds_shares_one_clock(tmp_path, monkeypatch, no_real_executor):
    """The replay's relative times ("{{now+25m}}") resolve against each client's own clock. A card compares the
    fingerprint the assistant took with the page's fresh read, so two clients built a second apart made a card look
    "changed" and Deny-only. This once failed under load; here each new client's clock is forced a second later."""
    from datetime import UTC, datetime, timedelta

    from fleetwatch.epiphan.replay import ReplayClient

    made: list[ReplayClient] = []
    base = datetime.now(UTC)

    class Ticking(ReplayClient):
        def __init__(self, directory, tools, now=None):
            super().__init__(directory, tools, now=now or base + timedelta(seconds=len(made) + 1))
            made.append(self)

    monkeypatch.setattr(cli, "ReplayClient", Ticking)
    from tests.test_assistant import FakeAnthropic, reply, text

    fake = FakeAnthropic(reply(text("Nothing needs attention.")))
    s = _settings(tmp_path, "observe", anthropic_api_key="sk-ant-test")
    page = cli._approve_page(s, str(FIXTURES), model_client=fake)
    asyncio.run(page.read_fleet())
    page.ask_fn("what needs attention")
    assert len(made) >= 2, "the page's client and the chat's reader"
    assert len({c.now for c in made}) == 1, [c.now for c in made]


def test_the_live_pages_sandbox_read_is_strict(tmp_path, monkeypatch):
    """A failed recorder read must fail the page's read (a Deny-only card), not show 'Not recording'."""
    from fleetwatch.epiphan.replay import ReplayClient
    from fleetwatch.heartbeat import FailedRead
    from fleetwatch.policy import load_tools

    class FlakyRecorder(ReplayClient):
        def __init__(self, *_a, **_k):
            super().__init__(FIXTURES, load_tools())

        async def call(self, tool, arguments=None):
            if tool == "get_recorder_status_for_devices":
                raise RuntimeError("the recorder read failed")
            return await super().call(tool, arguments)

    monkeypatch.setattr(cli, "EpiphanClient", FlakyRecorder)
    s = _settings(tmp_path, "propose", write_device_ids="0a1b2c3d")
    _sign_in_sandbox(s)
    page = cli._approve_page(s, None)
    with pytest.raises(FailedRead):
        asyncio.run(page.read_fleet())


def test_replay_reads_name_the_stream_endpoints_by_host_only(tmp_path, no_real_executor):
    page = cli._approve_page(_settings(tmp_path, "observe"), str(FIXTURES))
    fleet = asyncio.run(page.read_fleet())
    assert fleet.endpoints, "the replay sample lists stream destinations"
    assert all(e.host and "/" not in e.host for e in fleet.endpoints.values())
