"""Nightly sweep (#21): one calm summary a day, kept as history. It never opens findings; the heartbeat does that."""

from datetime import UTC, datetime, time, timedelta

from fleetwatch.model import Device, Fleet
from fleetwatch.policy import Policy, load_policy
from fleetwatch.state import State
from fleetwatch.sweep import due, render_sweep, run_sweep, summarize
from tests.conftest import NOW
from tests.test_heartbeat_replay import Capture, replay_client

POLICY = Policy(quiet_start=None, quiet_end=None, sweep_at=time(3, 0))


def _fleet(*devices: Device) -> Fleet:
    return Fleet(taken_at=NOW, devices={d.id: d for d in devices})


def test_due_once_per_local_day_after_the_time():
    at = time(3, 0)
    day = datetime(2026, 10, 7, tzinfo=UTC)
    assert not due(day.replace(hour=2, minute=59), at, None)
    assert due(day.replace(hour=3), at, None)
    assert due(day.replace(hour=10), at, None), "a machine that starts later still gets today's sweep"
    assert not due(day.replace(hour=10), at, day.replace(hour=3, minute=1))
    assert due((day + timedelta(days=1)).replace(hour=3), at, day.replace(hour=3, minute=1))
    assert not due(day.replace(hour=10), None, None), "no sweep_at, no sweep"


def test_summary_lists_offline_and_behind_firmware_in_scope():
    fleet = _fleet(
        Device(id="a", name="Main Stage", model="Pearl-2", firmware="4.24.6"),
        Device(id="b", name="Ballroom B", model="Pearl Mini", firmware="4.24.5"),
        Device(id="c", name="Lobby", model="Pearl Mini", online=False, firmware="4.24.6"),
        Device(id="d", name="Ignored", model="Pearl Mini", online=False),
    )
    s = summarize(fleet, Policy(exclude_devices=("Ignored",)), NOW, previous=None)
    assert (s.devices, s.online) == (3, 2)
    assert s.offline == ("Lobby",)
    assert s.behind == (("Ballroom B", "4.24.5", "4.24.6"),)
    assert s.newly_offline == () and s.back_online == ()


def test_summary_compares_with_the_previous_sweep():
    first = summarize(_fleet(Device(id="a", name="A", online=False), Device(id="b", name="B")), POLICY, NOW, None)
    second = summarize(
        _fleet(Device(id="a", name="A"), Device(id="b", name="B", online=False)),
        POLICY,
        NOW + timedelta(days=1),
        previous=first,
    )
    assert second.newly_offline == ("B",) and second.back_online == ("A",)


def test_render_is_calm_and_capped():
    many = _fleet(*[Device(id=str(i), name=f"Room {i:02d}", online=False) for i in range(14)])
    text = render_sweep(summarize(many, POLICY, NOW, None))
    assert text.startswith("*Nightly sweep* · 14 devices, 0 online")
    assert "Offline (14): Room 00, Room 01" in text and "and 4 more" in text
    for banned in ("critical", "urgent", "ALERT"):
        assert banned not in text
    healthy = render_sweep(summarize(_fleet(Device(id="a", name="A")), POLICY, NOW, None))
    assert "Everything is online and on the same firmware." in healthy


async def test_replay_sweep_records_history_and_posts_once():
    client, state, out = replay_client(), State(), Capture()
    async with client:
        posted = await run_sweep(client, state, POLICY, out, NOW)
    assert posted and out.posts[0].startswith("*Nightly sweep*")
    assert "Offline (11)" in out.posts[0] and "Hall A Auditorium (4.24.5; newest 4.24.6)" in out.posts[0]
    last = state.last_sweep()
    assert last is not None and last.posted_at == NOW and len(last.offline) == 11
    assert state.recent_audit("sweep", 1)


async def test_sweep_in_quiet_hours_waits_for_morning():
    quiet = Policy(quiet_start=time(0, 0), quiet_end=time(23, 59), sweep_at=time(3, 0))
    client, state, out = replay_client(), State(), Capture()
    async with client:
        posted = await run_sweep(client, state, quiet, out, NOW)
    assert not posted and out.posts == []
    pending = state.unposted_sweep()
    assert pending is not None and pending.posted_at is None


def test_snapshots_older_than_90_days_are_pruned():
    state = State()
    state.snapshot(NOW - timedelta(days=91), 5, 5)
    state.snapshot(NOW - timedelta(days=1), 5, 4)
    assert state.prune_snapshots(NOW - timedelta(days=90)) == 1
    assert len(state.snapshots_since(NOW - timedelta(days=365))) == 1


def test_sweep_at_loads_and_can_be_turned_off(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("sweep_at: '02:30'\n")
    assert load_policy(p).sweep_at == time(2, 30)
    p.write_text("sweep_at: ''\n")
    assert load_policy(p).sweep_at is None
    p.write_text("{}\n")
    assert load_policy(p).sweep_at == time(3, 0)


def test_history_text_per_day():
    from fleetwatch.sweep import render_history

    state = State()
    day1 = datetime(2026, 10, 5, 12, tzinfo=UTC)
    state.snapshot(day1, 10, 9)
    state.snapshot(day1 + timedelta(minutes=3), 10, 7)
    state.audit("digest", {"new": ["x"]}, day1)
    state.audit("readiness", {"event": "e", "verdict": "Ready"}, day1)
    text = render_history(state, since=day1 - timedelta(days=1), now=day1 + timedelta(hours=1))
    assert "2026-10-05" in text
    assert "10 devices, 7-9 online" in text and "1 digest" in text and "1 readiness check" in text


async def test_run_loop_sweeps_once_per_day():
    from fleetwatch.cli import _maybe_sweep

    client, state, out = replay_client(), State(), Capture()
    policy = Policy(quiet_start=None, quiet_end=None, sweep_at=time(0, 0))
    async with client:
        await _maybe_sweep(client, state, policy, out)
        await _maybe_sweep(client, state, policy, out)
    assert [p.startswith("*Nightly sweep*") for p in out.posts] == [True]
