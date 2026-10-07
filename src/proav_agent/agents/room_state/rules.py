"""Which state a room is in. Decides what the agent may say or (later) do. The trading analogue is the
market regime: live rooms are left alone, pre-class rooms get a readiness check."""

from datetime import datetime, timedelta

from proav_agent.model import Device, Event, RoomState


def room_state(device: Device, event: Event | None, now: datetime, lead: timedelta) -> RoomState:
    if not device.online:
        return RoomState.OFFLINE
    if device.recording or (event and event.start <= now and (event.end is None or now < event.end)):
        return RoomState.LIVE
    if event and now < event.start <= now + lead:
        return RoomState.PRE_CLASS
    return RoomState.IDLE
