"""Ready / Ready, with notes / Not ready, from what we can read without touching the room.
Ported from the Edge Claude Kit's /check-room. Space never makes a room Not ready."""

from datetime import datetime

from fleetwatch.epiphan.parse import STORAGE_WARNINGS
from fleetwatch.i18n import say
from fleetwatch.model import Device, Event, Readiness
from fleetwatch.policy import Policy

READY, NOTES, NOT_READY = "Ready", "Ready, with notes", "Not ready"


def check(device: Device, event: Event, policy: Policy | None = None, now: datetime | None = None) -> Readiness:
    """`now` lets the check say a room is already recording before the event starts; without it that note is skipped.
    The verdict is stored as the English constants above; only the notes are in the policy's language."""
    policy = policy or Policy()
    blockers: list[str] = []
    notes: list[str] = []
    if not device.online and device.is_camera:
        blockers.append(say(policy, "ready.camera_offline"))
    elif not device.online:
        blockers.append(say(policy, "ready.unit_offline"))
    else:
        if device.is_camera:
            notes.append(say(policy, "ready.ec20"))
        bad = [c.name for c in device.channels.values() if "channel_no_signal" in c.warnings]
        if bad:
            blockers.append(say(policy, "ready.no_picture", channels=", ".join(bad)))
        if now is not None and device.recording and event.start > now:
            notes.append(say(policy, "ready.already_recording"))
        if "source_no_signal" in device.warnings and not bad:
            notes.append(say(policy, "ready.idle_input"))
        if any(w in STORAGE_WARNINGS for w in device.warnings):
            notes.append(say(policy, "ready.low_space"))
    verdict = NOT_READY if blockers else (NOTES if notes else READY)
    return Readiness(event=event, device_name=device.name, verdict=verdict, notes=tuple(blockers + notes))
