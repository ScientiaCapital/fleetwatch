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


def test_refuses_to_start_without_a_sandbox_sign_in(tmp_path, capsys):
    s = _settings(tmp_path, "propose")
    with pytest.raises(SystemExit) as e:
        cli._approve_page(s, None)
    assert e.value.code != 0
    assert "fleetwatch login --sandbox" in capsys.readouterr().out


def test_starts_in_propose_mode_with_a_sandbox_sign_in(tmp_path):
    s = _settings(tmp_path, "propose")
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
