from datetime import timedelta

import pytest

from fleetwatch.agents.readiness.rules import NOT_READY, NOTES, READY, check
from fleetwatch.epiphan.parse import STORAGE_WARNINGS
from fleetwatch.model import Channel, Device, Event
from tests.conftest import NOW

EVENT = Event(device_id="1f000000001", title="Intro class", start=NOW + timedelta(minutes=20))


def pearl(*, online=True, recording=False, warnings=(), channel_warnings=()) -> Device:
    return Device(
        id="1f000000001",
        name="Room 204 Pearl Mini",
        model="Pearl Mini",
        online=online,
        warnings=list(warnings),
        channels={"1": Channel(id="1", name="Main", recording=recording, warnings=list(channel_warnings))},
    )


def test_a_clean_unit_is_ready():
    r = check(pearl(), EVENT)
    assert (r.verdict, r.notes) == (READY, ())


def test_an_offline_unit_is_not_ready():
    r = check(pearl(online=False), EVENT)
    assert r.verdict == NOT_READY
    assert "offline" in r.notes[0]


def test_an_offline_camera_says_its_picture_may_be_missing():
    camera = Device(id="1f000000002", name="Room 204 EC20", model="EC20", online=False)
    r = check(camera, EVENT)
    assert r.verdict == NOT_READY
    assert "camera" in r.notes[0]


def test_an_online_camera_gets_a_note_not_a_blocker():
    camera = Device(id="1f000000002", name="Room 204 EC20", model="EC20")
    r = check(camera, EVENT)
    assert r.verdict == NOTES
    assert "EC20" in r.notes[0]


def test_a_channel_without_signal_is_not_ready():
    r = check(pearl(channel_warnings=["channel_no_signal"]), EVENT)
    assert r.verdict == NOT_READY
    assert r.notes == ("No picture on Main",)


def test_an_unused_input_without_signal_is_a_note():
    r = check(pearl(warnings=["source_no_signal"]), EVENT)
    assert r.verdict == NOTES
    assert "no signal" in r.notes[0]


def test_a_used_input_without_signal_is_one_blocker_not_two():
    r = check(pearl(warnings=["source_no_signal"], channel_warnings=["channel_no_signal"]), EVENT)
    assert r.verdict == NOT_READY
    assert len(r.notes) == 1


@pytest.mark.parametrize("warning", sorted(STORAGE_WARNINGS))
def test_little_space_never_makes_a_room_not_ready(warning):
    r = check(pearl(warnings=[warning]), EVENT)
    assert r.verdict == NOTES
    assert "space" in r.notes[0]
