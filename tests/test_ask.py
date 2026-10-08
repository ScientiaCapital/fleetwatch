"""`fleetwatch ask`: typed questions answered from the state DB after one replay heartbeat. No network."""

from datetime import timedelta

import pytest

from fleetwatch.ask import HELP, answer
from fleetwatch.heartbeat import snapshot, tick
from fleetwatch.model import Finding, Priority
from fleetwatch.policy import Policy
from fleetwatch.state import State
from tests.conftest import NOW
from tests.test_heartbeat_replay import Capture, replay_client

POLICY = Policy(quiet_start=None, quiet_end=None)


@pytest.fixture
async def known():
    """State after one replay heartbeat, plus the fleet that heartbeat saw."""
    client, state = replay_client(), State()
    async with client:
        await tick(client, state, POLICY, Capture(), first_run=True, now=NOW)
        fleet = await snapshot(client, NOW)
    return state, fleet


def ask(known, q, *, live=True, policy=POLICY):
    state, fleet = known
    return answer(q, state, policy, fleet if live else None, now=NOW)


def test_is_room_ready_uses_the_live_check(known):
    out = ask(known, "Is Courtroom ready?")
    assert out.startswith("Courtroom · LAW 210 at ") and "Not ready" in out
    assert "- No picture on Camera 2" in out
    assert "Ready" in ask(known, "is science room 120b ready").splitlines()[0]


def test_room_without_a_live_fleet_falls_back_to_what_was_posted(known):
    out = ask(known, "is Courtroom ready", live=False)
    assert out.startswith("Courtroom: online")
    assert "Last check: LAW 210" in out and "Not ready" in out


def test_partial_room_name_finds_every_device_in_that_room(known):
    out = ask(known, "is room 204 ready for the keynote")
    assert "Room 204 Pearl Mini" in out and "Room 204 EC20" in out
    assert "Room 110" not in out and "Room 312" not in out


def test_offline_camera_is_fix_soon_and_its_pearl_keeps_fix_first(known):
    out = ask(known, "is room 204 ready")
    assert "- Fix soon: Room 204 EC20 is offline" in out
    assert "- Fix first: Room 204 Pearl Mini is offline" in out


def test_unknown_room_is_not_echoed(known):
    out = ask(known, "is <script>Narnia</script> ready")
    assert "couldn't find that room" in out and "Narnia" not in out and "<script>" not in out


def test_a_room_number_that_does_not_exist_matches_nothing(known):
    assert "couldn't find that room" in ask(known, "is room 999 ready")


def test_offline_lists_offline_rooms_only(known):
    out = ask(known, "what's offline?")
    assert out.splitlines()[0].endswith("offline:")
    assert "- Room 312 Pearl Mini" in out and "Broadcast Studio" not in out


def test_attention_groups_by_priority_in_digest_words(known):
    out = ask(known, "what needs attention")
    assert out.startswith("Fix first (")
    assert "Fix soon (" in out and out.index("Fix first") < out.index("Fix soon")
    assert "FYI: 3 Pearls" in out


def test_last_digest_counts_and_lists(known):
    out = ask(known, "read me the last digest")
    assert out.startswith("Last digest at ") and " new," in out and "Fix first (" in out


def test_spanish_keywords(known):
    assert "offline:" in ask(known, "¿qué está desconectado?")
    assert ask(known, "¿hay problemas?").startswith("Fix first (")
    assert "Not ready" in ask(known, "¿Está lista Courtroom?")


def test_nonsense_and_empty_get_help(known):
    assert ask(known, "tell me a joke") == HELP
    assert ask(known, "   ") == HELP


def test_vertical_word_shows_in_idle_room_answer(known):
    state, _ = known
    out = answer("is Broadcast Studio ready", state, Policy(vertical="courts"), None, now=NOW)
    assert "Broadcast Studio: online" in out
    if "nothing needs attention" in out:
        assert "No hearing is coming up" in out


def test_answers_never_carry_a_stream_key(known):
    state, _ = known
    leak = Finding(
        key="x:leak",
        priority=Priority.FIX_FIRST,
        device_id="x",
        device_name="X",
        what="Stream rtmp://live.example.com/app/sk_live_abc123secret failed",
    )
    state.reconcile(
        [leak],
        NOW + timedelta(minutes=3),
        timedelta(hours=4),
    )
    assert "sk_live_abc123secret" not in ask(known, "what needs attention")


def test_fresh_state_says_so():
    assert "haven't seen the fleet" in answer("what's offline", State(), POLICY, now=NOW)
    assert answer("what needs attention", State(), POLICY, now=NOW) == "Nothing needs attention right now."
    assert answer("last digest", State(), POLICY, now=NOW) == "No digest has been posted yet."


def test_cli_ask_with_replay_answers_once(monkeypatch, capsys):
    import sys

    from fleetwatch import cli

    monkeypatch.setattr(sys, "argv", ["fleetwatch", "ask", "--replay", "tests/fixtures", "is", "Courtroom", "ready"])
    cli.main()
    out = capsys.readouterr().out
    assert "Courtroom · LAW 210" in out and "Not ready" in out
    assert "Fleet check" not in out, "ask never prints the digest"


def test_cli_ask_without_a_question_explains(monkeypatch, capsys):
    import sys

    from fleetwatch import cli

    monkeypatch.setattr(sys, "argv", ["fleetwatch", "ask"])
    with pytest.raises(SystemExit):
        cli.main()
    assert "ask needs a question" in capsys.readouterr().err
