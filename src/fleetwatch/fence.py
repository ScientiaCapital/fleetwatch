"""The write fence (docs/design/approved-writes.md, "Fence"): which devices a change may touch.

A change is allowed only when a fence is set: a team ID the sandbox sign-in must report
(FLEETWATCH_WRITE_TEAM_ID), an allowlist of sandbox device IDs (FLEETWATCH_WRITE_DEVICE_IDS), or both. With neither,
every change is refused, so a sandbox sign-in that lands on the wrong team can't write to it. The allowlist doesn't
depend on Epiphan reporting a team ID. The assistant checks it when it proposes and the executor checks it again
when it runs.
"""

import re
from dataclasses import dataclass

from fleetwatch.config import Settings

_CHANNEL_ID = re.compile(r"([0-9a-f]{8,32})-[1-9][0-9]{0,2}")
_DEVICE_ID = re.compile(r"[0-9a-f]{8,32}")  # a master device ID, which is what the allowlist lists


def master_id(target: str) -> str:
    """The device a target ID is on. A channel ID is the master device ID plus "-N" (as batch_recording takes);
    a master ID has no "-", so stripping is unambiguous. The value has already passed the schema's ID pattern."""
    m = _CHANNEL_ID.fullmatch(target)
    return m.group(1) if m else target


@dataclass(frozen=True)
class Fence:
    team_id: str = ""
    device_ids: frozenset[str] = frozenset()
    invalid: tuple[str, ...] = ()  # allowlist entries that aren't master device IDs: ignored here, flagged by doctor

    @classmethod
    def from_settings(cls, settings: Settings) -> "Fence":
        entries = [d.strip().lower() for d in settings.write_device_ids.split(",") if d.strip()]
        ids = frozenset(e for e in entries if _DEVICE_ID.fullmatch(e))
        return cls(settings.write_team_id.strip(), ids, tuple(e for e in entries if not _DEVICE_ID.fullmatch(e)))

    @property
    def is_set(self) -> bool:
        return bool(self.team_id or self.device_ids)

    def allows(self, target: str) -> bool:
        """An unset fence allows nothing. A team ID alone allows any device the team reaches (the executor checks the
        team). An allowlist allows only its devices; a channel ID counts as its master device."""
        if not self.is_set:
            return False
        return not self.device_ids or master_id(target.lower()) in self.device_ids
