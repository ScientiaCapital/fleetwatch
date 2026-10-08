"""`/fleetwatch check <room>` in Slack, over Socket Mode (no inbound port). Read-only: it only calls `ask.answer`.

The slash command text is untrusted. It goes to `ask.answer` as a question and nowhere else: it never picks a tool,
never reaches Epiphan, and is never logged or stored. Only people on the policy.yaml allowlist get an answer, and
every reply is ephemeral (only the person who asked sees it).

Threads: slack_sdk's built-in Socket Mode client calls listeners on its own worker threads. SQLite connections
belong to the thread that opened them, so the listener hands the question to the heartbeat's event loop and waits
briefly for the reply. If the loop doesn't answer in time, the person gets a "try again" note; the heartbeat never
waits on Slack.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fleetwatch.ask import answer
from fleetwatch.config import Settings
from fleetwatch.model import Fleet
from fleetwatch.notify.slack import slack_escape
from fleetwatch.policy import Policy
from fleetwatch.redact import redact
from fleetwatch.state import State

log = logging.getLogger(__name__)

REFUSED = "Fleetwatch only answers people on its allowlist. Ask whoever runs Fleetwatch to add you."
BUSY = "Fleetwatch is busy right now. Try again in a moment."

# Slack wants the ack within 3 s. The reply rides on the ack, so the lookups must fit inside it.
GROUP_LOOKUP_SECONDS = 1.5
ANSWER_SECONDS = 1.0
CONNECT_SECONDS = 10.0
GROUP_TTL_SECONDS = 300


def _ephemeral(text: str) -> dict[str, str]:
    return {"response_type": "ephemeral", "text": text}


def handle(
    text: str,
    user_id: str,
    allowed: frozenset[str],
    state: State,
    policy: Policy,
    fleet: Fleet | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """The whole command: refuse anyone not allowed, otherwise answer the text as a question. Pure apart from
    reading `state`; no network, no tool call."""
    if not user_id or user_id not in allowed:
        return _ephemeral(REFUSED)
    return _ephemeral(slack_escape(answer(text, state, policy, fleet, now)))


class GroupMembers:
    """Members of one Slack user group (`usergroups.users.list`, needs `usergroups:read`). Cached for a few
    minutes. Any failure means nobody from the group, and the stale cache is dropped: fail closed."""

    def __init__(self, web: Any, group: str, ttl_seconds: float = GROUP_TTL_SECONDS, clock=time.monotonic):
        self._web, self._group, self._ttl, self._clock = web, group, ttl_seconds, clock
        self._cached: frozenset[str] | None = None
        self._at = 0.0

    def __call__(self) -> frozenset[str]:
        now = self._clock()
        if self._cached is not None and now - self._at < self._ttl:
            return self._cached
        try:
            r = self._web.usergroups_users_list(usergroup=self._group)
            users = frozenset(str(u) for u in (r.get("users") or []))
        except Exception as e:  # noqa: BLE001  (fail closed)
            log.warning("Slack user group lookup failed, allowing nobody from it: %s", redact(str(e)))
            self._cached = None
            return frozenset()
        self._cached, self._at = users, now
        return users


def allowed_users(policy: Policy, members: Callable[[], frozenset[str]] | None) -> frozenset[str]:
    ids = frozenset(policy.slack_allowed_user_ids)
    if policy.slack_allowed_usergroup and members is not None:
        ids |= members()
    return ids


class Listener:
    """Owns the Socket Mode client. `see()` keeps the latest fleet from the heartbeat; `keep_alive()` reconnects
    in the background if the first connect failed."""

    def __init__(self, client: Any, policy: Policy, state: State, members, loop: asyncio.AbstractEventLoop):
        self.client, self.policy, self.state, self.members, self.loop = client, policy, state, members, loop
        self.fleet: Fleet | None = None
        self._connecting: asyncio.Task | None = None
        client.socket_mode_request_listeners.append(self._on_request)

    def see(self, fleet: Fleet) -> None:
        self.fleet = fleet

    async def _answer(self, text: str, user_id: str, allowed: frozenset[str]) -> dict[str, str]:
        reply = handle(text, user_id, allowed, self.state, self.policy, self.fleet)
        self.state.audit("slack_command", {"user": user_id, "allowed": reply["text"] != REFUSED})
        return reply

    def _on_request(self, client: Any, req: Any) -> None:
        """Runs on a Socket Mode worker thread. Always acks; a slash command's reply rides on the ack."""
        from slack_sdk.socket_mode.response import SocketModeResponse

        payload = None
        if req.type == "slash_commands":
            try:
                p = req.payload or {}
                user_id, text = str(p.get("user_id") or ""), str(p.get("text") or "")
                allowed = allowed_users(self.policy, self.members)
                fut = asyncio.run_coroutine_threadsafe(self._answer(text, user_id, allowed), self.loop)
                try:
                    payload = fut.result(timeout=ANSWER_SECONDS)
                except TimeoutError:
                    fut.cancel()
                    payload = _ephemeral(BUSY)
            except Exception as e:  # noqa: BLE001  (a bad request must not stop the listener)
                log.warning("Slack command failed: %s", redact(str(e)))
                payload = _ephemeral(BUSY)
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id, payload=payload))

    def keep_alive(self) -> asyncio.Task | None:
        """Start a background connect if we're not connected and not already trying. Never blocks the beat."""
        if self.client.is_connected() or (self._connecting is not None and not self._connecting.done()):
            return None
        self._connecting = asyncio.create_task(self._connect())
        return self._connecting

    async def _connect(self) -> None:
        try:
            await asyncio.to_thread(self.client.connect_to_new_endpoint)
        except Exception as e:  # noqa: BLE001  (retried on the next beat)
            log.warning("Slack commands can't connect yet, retrying next beat: %s", redact(str(e)))

    def close(self) -> None:
        try:
            self.client.close()
        except Exception as e:  # noqa: BLE001
            log.debug("Slack socket close: %s", e)


async def start_listener(settings: Settings, policy: Policy, state: State, *, socket_factory=None) -> Listener | None:
    """Start answering /fleetwatch, but only when FLEETWATCH_SLACK_APP_TOKEN is set. Never raises."""
    if not settings.slack_app_token:
        return None
    try:
        if socket_factory is None:
            from slack_sdk.socket_mode.builtin import SocketModeClient as socket_factory
        members = None
        if policy.slack_allowed_usergroup and settings.slack_bot_token:
            from slack_sdk import WebClient

            web = WebClient(token=settings.slack_bot_token, timeout=GROUP_LOOKUP_SECONDS)
            members = GroupMembers(web, policy.slack_allowed_usergroup)
        client = socket_factory(app_token=settings.slack_app_token)
        listener = Listener(client, policy, state, members, asyncio.get_running_loop())
    except Exception as e:  # noqa: BLE001  (Slack commands are optional; the heartbeat goes on)
        log.warning("Slack commands are off: %s", redact(str(e)))
        return None
    task = listener.keep_alive()
    if task is not None:
        await asyncio.wait({task}, timeout=CONNECT_SECONDS)
    return listener
