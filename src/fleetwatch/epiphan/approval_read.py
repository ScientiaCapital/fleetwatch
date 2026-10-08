"""The approval page's fresh read: the usual fleet snapshot plus the team's stream destinations.

One more guarded read through the same client (`get_stream_endpoints` is a read tool), so the card can show a
destination's name and host instead of a bare ID. The result goes through redact() inside the client, and only the
name and host are kept (see parse_stream_endpoints). A failed read leaves `fleet.endpoints` as None, which the card
treats as "can't show it, so Deny only"."""

import logging
from datetime import datetime

from fleetwatch.epiphan.mcp import EpiphanClient
from fleetwatch.epiphan.parse import parse_stream_endpoints
from fleetwatch.heartbeat import snapshot
from fleetwatch.model import Fleet
from fleetwatch.redact import redact

log = logging.getLogger(__name__)


async def read_for_approval(client: EpiphanClient, now: datetime, *, strict: bool = False) -> Fleet:
    """`strict` is passed to snapshot: the live page reads strictly, so a failed recorder or event read fails the card.
    A failed endpoints read never raises: it leaves `endpoints` None (a Deny-only card for a stream)."""
    fleet = await snapshot(client, now, strict=strict)
    try:
        fleet.endpoints = parse_stream_endpoints(await client.call("get_stream_endpoints"))
    except Exception as e:  # noqa: BLE001 - a failed read only means the card can't name streams
        log.warning("get_stream_endpoints failed: %s", redact(str(e)))
    return fleet
