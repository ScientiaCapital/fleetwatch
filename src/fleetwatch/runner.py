"""`fleetwatch run`: heartbeat after heartbeat, and recover when the network or the session goes away.

A failed beat closes the MCP session and the next beat opens a fresh one, so a dropped hotspot doesn't leave a
dead session behind. After `MAX_FAILED_BEATS` failures in a row the process exits with `EXIT_TEMPFAIL`, and
systemd (`Restart=on-failure`), launchd (`KeepAlive` on a failed exit) or Docker (`restart: unless-stopped`)
starts it again from clean. A good beat resets the count.

A refresh token Epiphan refuses is different: restarting can't fix it. The process logs "Sign-in expired: run
fleetwatch login" and exits with `EXIT_CONFIG` (78), which the systemd unit is told not to restart.

So is a fleet Fleetwatch can't make sense of (`UnreadableFleet`: the parser, scanner or reconcile step raised on what
Epiphan sent). A restart reads the same data, so the process keeps beating on the same session. After
`UNREADABLE_BEATS` in a row it posts one fixed line saying it can't read the fleet, and another when it can again
(#124). Every failed beat is in the audit log as `beat_failed`, for `status` and `doctor`.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fleetwatch.epiphan.auth import SIGN_IN_EXPIRED
from fleetwatch.epiphan.mcp import SignInDead
from fleetwatch.heartbeat import UnreadableFleet, tick
from fleetwatch.i18n import t
from fleetwatch.notify import Notifier
from fleetwatch.policy import Policy
from fleetwatch.redact import redact
from fleetwatch.state import State

log = logging.getLogger("fleetwatch")

MAX_FAILED_BEATS = 3
UNREADABLE_BEATS = 3  # unreadable beats in a row before the channel is told
EXIT_TEMPFAIL = 75  # EX_TEMPFAIL from sysexits.h: try again later
EXIT_CONFIG = 78  # EX_CONFIG: the sign-in is dead; systemd's RestartPreventExitStatus=78 stops the restarts
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
    failures = unreadable = 0
    told = False  # the channel has been told Fleetwatch can't read the fleet
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
                failures = unreadable = 0
                if told and notifier.post(t(policy.language, "run.can_read_again")):
                    told = False
            except SignInDead:
                log.error(SIGN_IN_EXPIRED)
                raise SystemExit(EXIT_CONFIG) from None
            except UnreadableFleet as e:  # Epiphan answered, so the session is fine: keep it, and don't exit
                failures, unreadable = 0, unreadable + 1
                reason = redact(str(e))
                log.warning("heartbeat couldn't read the fleet (%d in a row): %s", unreadable, reason)
                state.audit("beat_failed", {"reason": reason})
                if unreadable >= UNREADABLE_BEATS and not told:
                    told = notifier.post(t(policy.language, "run.cant_read", n=unreadable))
            except (Exception, asyncio.CancelledError) as e:  # counted; the next beat retries on a new session
                # The MCP client runs its own task group in this task. When a read stalls (a Mac waking from sleep),
                # that scope can cancel this task from inside, and a CancelledError comes out with nobody asking
                # to stop. Close the session first: that exits the client's scope and drops its cancel. If the
                # task is still being cancelled after that, the cancel is real (Ctrl+C, shutdown): pass it on.
                if client is not None:
                    await _close(client)
                    client = None
                if isinstance(e, asyncio.CancelledError) and (task := asyncio.current_task()) and task.cancelling():
                    raise
                failures += 1
                reason = redact(str(e) or type(e).__name__)
                log.warning("heartbeat failed (%d in a row): %s", failures, reason)
                state.audit("beat_failed", {"reason": f"{type(e).__name__}: {reason}"[:300]})
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
