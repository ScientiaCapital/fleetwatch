"""`fleetwatch run`: heartbeat after heartbeat, and recover when the network or the session goes away.

A failed beat closes the MCP session and the next beat opens a fresh one, so a dropped hotspot doesn't leave a
dead session behind. After `MAX_FAILED_BEATS` failures in a row the process exits with `EXIT_TEMPFAIL`, and
systemd (`Restart=on-failure`), launchd (`KeepAlive` on a failed exit) or Docker (`restart: unless-stopped`)
starts it again from clean. A good beat resets the count.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fleetwatch.heartbeat import tick
from fleetwatch.notify import Notifier
from fleetwatch.policy import Policy
from fleetwatch.redact import redact
from fleetwatch.state import State

log = logging.getLogger("fleetwatch")

MAX_FAILED_BEATS = 3
EXIT_TEMPFAIL = 75  # EX_TEMPFAIL from sysexits.h: try again later
_sleep = asyncio.sleep


async def _close(client: Any) -> None:
    try:
        await client.__aexit__(None, None, None)
    except Exception as e:  # noqa: BLE001  (a session that already broke may not close cleanly)
        log.debug("closing the Epiphan session: %s", redact(str(e)))


async def run_loop(
    make_client: Callable[[], Any],
    state: State,
    policy: Policy,
    notifier: Notifier,
    *,
    first_run: bool,
    slash: Any = None,
    after_beat: Callable[[Any], Awaitable[None]] | None = None,
    sleep: Callable[[float], Awaitable[Any]] | None = None,
) -> None:
    """Runs until cancelled, or raises SystemExit(EXIT_TEMPFAIL) after too many failed beats in a row."""
    sleep = sleep or _sleep
    failures = 0
    client = None
    try:
        while True:
            try:
                if client is None:
                    fresh = make_client()
                    try:
                        await fresh.__aenter__()
                    except BaseException:
                        await _close(fresh)
                        raise
                    client = fresh
                await tick(client, state, policy, notifier, first_run=first_run, on_fleet=slash and slash.see)
                failures = 0
            except Exception as e:  # noqa: BLE001  (counted; the next beat retries on a new session)
                failures += 1
                log.warning("heartbeat failed (%d in a row): %s", failures, redact(str(e)))
                if client is not None:
                    await _close(client)
                    client = None
                if failures >= MAX_FAILED_BEATS:
                    log.error("%d heartbeats failed in a row; exiting so the service manager restarts", failures)
                    raise SystemExit(EXIT_TEMPFAIL) from None
            first_run = False
            if client is not None and after_beat is not None:
                try:
                    await after_beat(client)
                except Exception as e:  # noqa: BLE001  (a failed sweep retries on the next beat)
                    log.warning("sweep failed: %s", redact(str(e)))
            if slash:
                slash.keep_alive()
            await sleep(policy.heartbeat_seconds)
    finally:
        if client is not None:
            await _close(client)
