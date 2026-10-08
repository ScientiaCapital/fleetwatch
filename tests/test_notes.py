"""Per-room notes (#23): typed by people, so untrusted. Redacted, cleaned and capped before they are stored, and
shown under that room's finding in the digest and in `ask` answers."""

import sqlite3
from datetime import timedelta

import pytest

from fleetwatch.ask import answer
from fleetwatch.heartbeat import tick
from fleetwatch.model import Finding, Priority
from fleetwatch.notes import MAX_NOTE, add_note, clean_note, list_notes
from fleetwatch.notify.digest import render_digest
from fleetwatch.policy import Policy
from fleetwatch.state import State
from tests.conftest import NOW
from tests.test_heartbeat_replay import Capture, replay_client

POLICY = Policy(quiet_start=None, quiet_end=None)


@pytest.fixture
async def known():
    """State after one replay heartbeat, so the fixture rooms are known."""
    client, state = replay_client(), State()
    async with client:
        await tick(client, state, POLICY, Capture(), first_run=True, now=NOW)
    return state


def _id(state: State, name: str) -> str:
    (d,) = [d for d in state.devices() if d.name == name]
    return d.id


# --- cleaning -----------------------------------------------------------------------------------------------
def test_a_stream_key_is_redacted():
    out = clean_note("new stream key: live_9f8e7d6c5b4a, pushed to rtmp://ingest.example.com/live/abc123")
    assert "9f8e7d6c5b4a" not in out and "abc123" not in out
    assert "[redacted]" in out


def test_control_characters_are_stripped():
    out = clean_note("Projector\x1b[31m bulb\r\nreplaced\x00 \u202eevil\u200b today\t")
    assert out == "Projector[31m bulb replaced evil today"
    assert all(ch.isprintable() for ch in out)


def test_the_280_character_cap_holds():
    assert len(clean_note("x" * 1000)) == MAX_NOTE == 280
    assert len(clean_note("word " * 200)) <= MAX_NOTE


def test_an_empty_note_is_refused(known):
    code, msg = add_note(known, "Courtroom", " \x00\n ", "alex", NOW)
    assert code != 0 and "empty" in msg
    assert known.notes() == []


# --- which room ---------------------------------------------------------------------------------------------
def test_an_ambiguous_room_is_refused_and_lists_the_candidates(known):
    code, msg = add_note(known, "Room 204", "Bulb replaced", "alex", NOW)
    assert code != 0
    assert "Room 204 Pearl Mini" in msg and "Room 204 EC20" in msg
    assert known.notes() == []


def test_an_unknown_room_is_refused(known):
    code, msg = add_note(known, "Room 999", "Bulb replaced", "alex", NOW)
    assert code != 0 and "couldn't find" in msg
    assert known.notes() == []


def test_a_note_is_saved_against_the_one_matching_room(known):
    code, msg = add_note(known, "room 312 pearl", "Bulb replaced; spare in the cabinet", "alex", NOW)
    assert code == 0 and "Room 312 Pearl Mini" in msg
    (n,) = known.notes()
    assert (n.device_id, n.note, n.author, n.at) == (
        _id(known, "Room 312 Pearl Mini"),
        "Bulb replaced; spare in the cabinet",
        "alex",
        NOW,
    )


def test_the_author_is_cleaned_too(known):
    add_note(known, "Courtroom", "Mic swapped", "al\x1bex\n" + "y" * 100, NOW)
    (n,) = known.notes()
    assert "\x1b" not in n.author and "\n" not in n.author and len(n.author) <= 40


# --- listing ------------------------------------------------------------------------------------------------
def test_notes_list_by_room_and_by_search(known):
    add_note(known, "Courtroom", "Camera 2 cable reseated", "alex", NOW)
    add_note(known, "Main Stage", "Fan is loud", "sam", NOW + timedelta(minutes=5))

    everything = list_notes(known)
    assert everything.index("Main Stage") < everything.index("Courtroom")  # newest first
    assert "Fan is loud" not in list_notes(known, room="Courtroom")
    assert "Camera 2 cable reseated" in list_notes(known, room="Courtroom")
    searched = list_notes(known, search="FAN")
    assert "Fan is loud" in searched and "Courtroom" not in searched
    assert list_notes(known, search="nothing like this") == "No notes match."


def test_search_text_is_not_a_sql_pattern(known):
    add_note(known, "Courtroom", "Camera 2 cable reseated", "alex", NOW)
    assert list_notes(known, search="%") == "No notes match."
    assert list_notes(known, search="_") == "No notes match."


# --- where notes show ---------------------------------------------------------------------------------------
def test_a_note_shows_under_the_right_device_in_the_digest():
    a = Finding("a:off", Priority.FIX_FIRST, "a", "Room 312 Pearl Mini", "Room 312 Pearl Mini is offline")
    b = Finding("b:fw", Priority.FIX_SOON, "b", "Hall A Auditorium", "Hall A Auditorium runs firmware 4.24.5")
    c = Finding("a:hot", Priority.FIX_SOON, "a", "Room 312 Pearl Mini", "Room 312 Pearl Mini is running warm")
    s = State()
    s.add_note("a", "Bulb replaced", "alex", NOW)
    text = render_digest([a, b, c], [], [], notes=s.notes_by_device({"a", "b"}))
    lines = text.splitlines()
    i = next(n for n, line in enumerate(lines) if "Room 312 Pearl Mini is offline" in line)
    assert "“Bulb replaced”" in lines[i + 1] and "alex" in lines[i + 1]
    assert sum("Bulb replaced" in line for line in lines) == 1  # once per device, not once per finding
    assert not any("Bulb replaced" in line for line in lines[: i + 1])


async def test_the_replay_heartbeat_puts_the_note_under_that_room(known):
    fresh, out, client = State(), Capture(), replay_client()
    fresh.add_note(_id(known, "Room 312 Pearl Mini"), "Power strip was switched off", "alex", NOW)
    async with client:
        await tick(client, fresh, POLICY, out, first_run=True, now=NOW)
    lines = out.posts[0].splitlines()
    i = next(n for n, line in enumerate(lines) if line.startswith("• *Fix first*: Room 312 Pearl Mini is offline"))
    assert "Power strip was switched off" in lines[i + 1]
    assert sum("Power strip" in line for line in lines) == 1


def test_digest_without_notes_is_unchanged():
    a = Finding("a:off", Priority.FIX_FIRST, "a", "Room 312 Pearl Mini", "Room 312 Pearl Mini is offline")
    assert render_digest([a], [], []) == render_digest([a], [], [], notes={})


def test_ask_shows_the_notes_for_that_room(known):
    add_note(known, "Room 312 Pearl Mini", "Power strip was switched off", "alex", NOW)
    add_note(known, "Main Stage", "Fan is loud", "sam", NOW)
    out = answer("how is room 312 pearl mini", known, POLICY, None, now=NOW)
    assert "Power strip was switched off" in out and "Fan is loud" not in out


def test_a_redacted_note_reads_cleanly_in_ask(known):
    add_note(known, "Courtroom", "stream key: live_9f8e7d6c", "alex", NOW)
    out = answer("how is Courtroom", known, POLICY, None, now=NOW)
    assert "“stream key: [redacted]”" in out and "9f8e7d6c" not in out


async def test_ask_shows_notes_on_the_live_readiness_branch_too(known):
    from fleetwatch.heartbeat import snapshot

    add_note(known, "Courtroom", "Camera 2 cable reseated", "alex", NOW)
    async with replay_client() as client:
        fleet = await snapshot(client, NOW)
    out = answer("is Courtroom ready", known, POLICY, fleet, now=NOW)
    assert out.startswith("Courtroom · LAW 210")
    assert "Camera 2 cable reseated" in out


# --- upgrade ------------------------------------------------------------------------------------------------
def test_an_old_database_without_the_notes_table_upgrades(tmp_path):
    path = tmp_path / "state.db"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE findings (
          key TEXT PRIMARY KEY, device_id TEXT, device_name TEXT, priority TEXT, what TEXT, fyi INTEGER,
          first_seen TEXT, last_seen TEXT, last_sent TEXT, resolved_at TEXT);
        CREATE TABLE devices (
          id TEXT PRIMARY KEY, name TEXT, model TEXT, group_name TEXT, online INTEGER, firmware TEXT,
          recording INTEGER, last_seen TEXT);
        INSERT INTO devices VALUES ('x','Lobby Pearl Nano','Pearl Nano','',1,'4.24.6',0,'2026-10-01T00:00:00+00:00');
        """
    )
    old.commit()
    old.close()

    s = State(path)
    assert s.notes() == []
    code, _ = add_note(s, "Lobby", "Moved to the left rack", "alex", NOW)
    assert code == 0
    assert [n.note for n in State(path).notes()] == ["Moved to the left rack"]


# --- cli ----------------------------------------------------------------------------------------------------
def test_cli_note_and_notes_use_the_local_state_db(tmp_path, monkeypatch, capsys):
    import sys

    from fleetwatch import cli
    from fleetwatch.model import Device, Fleet

    db = tmp_path / "state.db"
    State(db).record_devices(
        Fleet(taken_at=NOW, devices={"d1": Device(id="d1", name="Main Stage"), "d2": Device(id="d2", name="Lobby")}),
        NOW,
    )
    monkeypatch.setenv("FLEETWATCH_STATE_DB", str(db))

    monkeypatch.setattr(sys, "argv", ["fleetwatch", "note", "main stage", "Fan is loud", "--author", "sam"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0 and "Saved a note on Main Stage." in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["fleetwatch", "notes", "--search", "fan"])
    cli.main()
    assert "Main Stage · sam: Fan is loud" in capsys.readouterr().out
