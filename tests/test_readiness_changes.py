"""Before an event, a room's readiness is posted once, and again only when the verdict changes. Runs the real
heartbeat over copies of the calm sample with one room changed. No network, no sign-in."""

import json
import shutil
import sqlite3
from datetime import time, timedelta
from pathlib import Path

from fleetwatch.epiphan.replay import ReplayClient
from fleetwatch.heartbeat import tick
from fleetwatch.model import Event, Readiness
from fleetwatch.notify.digest import render_readiness
from fleetwatch.policy import Policy, load_tool_policy
from fleetwatch.state import State
from tests.conftest import NOW

ROOT = Path(__file__).resolve().parents[1]
CALM = ROOT / "tests/fixtures/calm"
ROOM = "1f000000013"  # Main Stage Pearl Nano, "Opening demo" starts 20 minutes after NOW
EVENT_KEY = f"{ROOM}:demo-opening"
AWAKE = Policy(quiet_start=None, quiet_end=None)


class Capture:
    def __init__(self):
        self.posts: list[str] = []

    def post(self, text: str) -> bool:
        self.posts.append(text)
        return True

    def readiness(self) -> list[str]:
        return [p for p in self.posts if p.startswith("*Main Stage Pearl Nano* · ")]


def sample(tmp_path: Path, name: str, change=None) -> Path:
    """A copy of the calm sample; `change(device)` edits the Main Stage Pearl Nano in the device list."""
    out = tmp_path / name
    shutil.copytree(CALM, out)
    if change is not None:
        path = out / "get_devices_in_my_team.json"
        raw = json.loads(path.read_text())
        change(next(d for d in raw["devices"] if d["Id"] == ROOM))
        path.write_text(json.dumps(raw))
    return out


def offline(d):
    d["Status"] = "offline"


def no_picture(d):
    d["Channels"][0]["Warnings"] = [{"id": "channel_no_signal"}]


def spare_input_dark(d):
    d["Warnings"] = [{"id": "source_no_signal"}]


async def beat(directory: Path, state: State, out: Capture, minutes: int, policy: Policy = AWAKE) -> None:
    async with ReplayClient(directory, load_tool_policy(ROOT / "tool_policy.yaml"), now=NOW) as client:
        await tick(client, state, policy, out, now=NOW + timedelta(minutes=minutes))


# --- wording ----------------------------------------------------------------------------------------


def _r(verdict: str, notes: tuple[str, ...] = ()) -> Readiness:
    event = Event(device_id="d", title="LAW 210", start=NOW, id="e1")
    return Readiness(event=event, device_name="Courtroom", verdict=verdict, notes=notes)


def test_first_check_wording_is_unchanged():
    assert render_readiness(_r("Ready")).endswith(": *Ready*")


def test_change_to_not_ready_says_what_it_was_and_why():
    text = render_readiness(_r("Not ready", ("The unit is offline, so the event won't record",)), was="Ready")
    head, reason = text.splitlines()
    assert head.startswith("*Courtroom* · LAW 210 at ")
    assert head.endswith(": *Now not ready* (was Ready)")
    assert reason == "• The unit is offline, so the event won't record"


def test_change_to_ready_says_ready_now():
    assert render_readiness(_r("Ready"), was="Not ready").endswith(": *Ready now* (was Not ready)")


def test_change_to_ready_with_notes():
    text = render_readiness(_r("Ready, with notes", ("One input has no signal",)), was="Ready")
    assert text.splitlines()[0].endswith(": *Ready now, with notes* (was Ready)")


# --- state ------------------------------------------------------------------------------------------


def test_last_posted_verdict_per_occurrence():
    s = State()
    assert s.readiness_verdict("d:e1") is None
    s.record_readiness(_r("Ready"), NOW)
    s.record_readiness(_r("Not ready"), NOW + timedelta(minutes=3))
    assert s.readiness_verdict("d:e1") == "Not ready"


# --- heartbeat --------------------------------------------------------------------------------------


async def test_ready_then_offline_posts_now_not_ready_once(tmp_path):
    calm, down = sample(tmp_path, "calm"), sample(tmp_path, "down", offline)
    state, out = State(), Capture()
    await beat(calm, state, out, 0)
    await beat(down, state, out, 3)
    await beat(down, state, out, 6)
    first, change = out.readiness()
    assert first.endswith(": *Ready*")
    head = change.splitlines()[0]
    assert head.endswith(": *Now not ready* (was Ready)") and "Opening demo" in head
    assert "The unit is offline" in change
    assert state.readiness_verdict(EVENT_KEY) == "Not ready"


async def test_same_verdict_never_reposts(tmp_path):
    calm = sample(tmp_path, "calm")
    state, out = State(), Capture()
    for minutes in (0, 3, 6, 9):
        await beat(calm, state, out, minutes)
    assert len(out.readiness()) == 1


async def test_same_verdict_with_new_reasons_does_not_repost(tmp_path):
    down, dark = sample(tmp_path, "down", offline), sample(tmp_path, "dark", no_picture)
    state, out = State(), Capture()
    await beat(down, state, out, 0)
    await beat(dark, state, out, 3)  # still Not ready, for a different reason
    assert len(out.readiness()) == 1


async def test_fixed_room_posts_ready_now(tmp_path):
    dark, calm = sample(tmp_path, "dark", no_picture), sample(tmp_path, "calm")
    state, out = State(), Capture()
    await beat(dark, state, out, 0)
    await beat(calm, state, out, 3)
    first, change = out.readiness()
    assert first.splitlines()[0].endswith(": *Not ready*")
    assert change == change.splitlines()[0] and change.endswith(": *Ready now* (was Not ready)")


async def test_notes_level_change_posts(tmp_path):
    calm, spare = sample(tmp_path, "calm"), sample(tmp_path, "spare", spare_input_dark)
    state, out = State(), Capture()
    await beat(calm, state, out, 0)
    await beat(spare, state, out, 3)
    change = out.readiness()[-1]
    assert change.splitlines()[0].endswith(": *Ready now, with notes* (was Ready)")
    assert "One input has no signal" in change


async def test_changes_get_through_quiet_hours_both_ways(tmp_path):
    """Readiness checks already post in quiet hours (an early event has people on it); changes do too."""
    night = Policy(quiet_start=time(0, 0), quiet_end=time(23, 59))
    calm, down = sample(tmp_path, "calm"), sample(tmp_path, "down", offline)
    state, out = State(), Capture()
    await beat(calm, state, out, 0, night)
    await beat(down, state, out, 3, night)
    await beat(calm, state, out, 6, night)
    heads = [p.splitlines()[0] for p in out.readiness()]
    assert heads[0].endswith(": *Ready*")
    assert heads[1].endswith(": *Now not ready* (was Ready)")
    assert heads[2].endswith(": *Ready now* (was Not ready)")


async def test_short_flap_posts_each_change_once_per_heartbeat(tmp_path):
    """No damping: each post is true when it is sent, and one heartbeat posts at most once per event."""
    calm, down = sample(tmp_path, "calm"), sample(tmp_path, "down", offline)
    state, out = State(), Capture()
    await beat(calm, state, out, 0)
    await beat(down, state, out, 3)
    await beat(calm, state, out, 6)
    assert len(out.readiness()) == 3


async def test_no_repost_after_the_event_starts(tmp_path):
    calm, down = sample(tmp_path, "calm"), sample(tmp_path, "down", offline)
    state, out = State(), Capture()
    await beat(calm, state, out, 0)
    await beat(down, state, out, 20)  # the start
    await beat(down, state, out, 23)
    assert len(out.readiness()) == 1
    assert state.readiness_verdict(EVENT_KEY) == "Ready"


async def test_old_database_keeps_its_verdict_and_reposts_on_change(tmp_path):
    """A state.db from the first schema (readiness with only event_key, posted_at, verdict) still works."""
    path = tmp_path / "state.db"
    old = sqlite3.connect(path)
    old.executescript(
        f"""
        CREATE TABLE readiness (event_key TEXT PRIMARY KEY, posted_at TEXT, verdict TEXT);
        INSERT INTO readiness VALUES ('{EVENT_KEY}','2026-10-07T14:59:00+00:00','Ready');
        """
    )
    old.commit()
    old.close()

    state, out = State(path), Capture()
    assert state.readiness_verdict(EVENT_KEY) == "Ready"
    await beat(sample(tmp_path, "calm"), state, out, 0)
    assert out.readiness() == [], "already posted as Ready before the upgrade"
    await beat(sample(tmp_path, "down", offline), state, out, 3)
    (change,) = out.readiness()
    assert change.splitlines()[0].endswith(": *Now not ready* (was Ready)")
    assert State(path).readiness_verdict(EVENT_KEY) == "Not ready"
