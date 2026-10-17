from datetime import timedelta

import pytest

from fleetwatch.agents.room_state.rules import room_state
from fleetwatch.model import Channel, Device, Event, RoomState
from tests.conftest import NOW

LEAD = timedelta(minutes=30)


def device(*, online: bool = True, recording: bool = False) -> Device:
    return Device(
        id="1f000000001",
        name="Room 204 Pearl Mini",
        online=online,
        channels={"1": Channel(id="1", name="Main", recording=recording)},
    )


def event(start_in: timedelta, length: timedelta | None = timedelta(hours=1)) -> Event:
    start = NOW + start_in
    return Event(device_id="1f000000001", title="Intro class", start=start, end=start + length if length else None)


@pytest.mark.parametrize(
    ("dev", "ev", "expected"),
    [
        (device(online=False), None, RoomState.OFFLINE),
        (device(online=False, recording=True), event(timedelta(0)), RoomState.OFFLINE),  # offline wins over live
        (device(recording=True), None, RoomState.LIVE),  # recording with no event
        (device(), event(timedelta(minutes=-10)), RoomState.LIVE),  # inside the event
        (device(), event(timedelta(0)), RoomState.LIVE),  # starts exactly now
        (device(), event(timedelta(minutes=-10), length=None), RoomState.LIVE),  # no end: live from the start on
        (device(), event(timedelta(minutes=-60)), RoomState.IDLE),  # ended exactly now: not live
        (device(), event(timedelta(minutes=-90)), RoomState.IDLE),  # already over
        (device(), event(timedelta(minutes=10)), RoomState.PRE_CLASS),
        (device(), event(LEAD), RoomState.PRE_CLASS),  # lead boundary is inclusive
        (device(), event(LEAD + timedelta(seconds=1)), RoomState.IDLE),  # just past the window
        (device(), None, RoomState.IDLE),
    ],
)
def test_room_state(dev, ev, expected):
    assert room_state(dev, ev, NOW, LEAD) is expected
