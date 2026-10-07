"""Ready / Ready, with notes / Not ready, from what we can read without touching the room.
Ported from the Edge Claude Kit's /check-room. Space never makes a room Not ready."""

from proav_agent.epiphan.parse import STORAGE_WARNINGS
from proav_agent.model import Device, Event, Readiness

READY, NOTES, NOT_READY = "Ready", "Ready, with notes", "Not ready"


def check(device: Device, event: Event) -> Readiness:
    blockers: list[str] = []
    notes: list[str] = []
    if not device.online:
        blockers.append("The unit is offline, so the class won't record")
    else:
        if device.is_camera:
            notes.append("This is an EC20 camera: it can't record or stream on its own")
        bad = [c.name for c in device.channels.values() if "channel_no_signal" in c.warnings]
        if bad:
            blockers.append(f"No picture on {', '.join(bad)}")
        if device.recording and event.start > device_now(event):
            notes.append("Already recording; the scheduled class may start a second file")
        if "source_no_signal" in device.warnings and not bad:
            notes.append("One input has no signal (not used by a channel, as far as we can see)")
        if any(w in STORAGE_WARNINGS for w in device.warnings):
            notes.append("Little local space left; fine when recordings upload to your CMS")
    verdict = NOT_READY if blockers else (NOTES if notes else READY)
    return Readiness(event=event, device_name=device.name, verdict=verdict, notes=tuple(blockers + notes))


def device_now(event: Event):
    """The reference 'now' is the event's start; kept as a hook for tests."""
    return event.start
