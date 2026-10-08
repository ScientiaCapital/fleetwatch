"""Where the agent talks: Slack, Microsoft Teams, or both; the console when neither is configured.

Templates write Slack mrkdwn. Each channel converts or escapes it on its own path, so one channel's rules never
leak into another's.
"""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from fleetwatch.config import Settings

log = logging.getLogger(__name__)


class Notifier(Protocol):
    def post(self, text: str) -> bool:
        """Send one message. True if it went out; never raises into the heartbeat."""
        ...


class ConsoleNotifier:
    """Prints instead of posting: the default when no channel is configured, and in tests and demos."""

    def __init__(self, label: str):
        self.label = label

    def post(self, text: str) -> bool:
        # Says "preview" so a screen showing this output never reads like a real post to the channel.
        header = f"console preview for {self.label}" if self.label else "console preview"
        print(f"\n[{header}]\n{text}\n")
        return True


class FanOut:
    """Posts to every channel. Succeeds if any channel did, so a post that reached Slack isn't repeated there
    next heartbeat just because Teams was down."""

    def __init__(self, notifiers: Sequence[Notifier]):
        self.notifiers = list(notifiers)

    def post(self, text: str) -> bool:
        ok = False
        for n in self.notifiers:
            try:
                ok = n.post(text) or ok
            except Exception as e:  # noqa: BLE001  (one broken channel must not silence the others)
                log.warning("%s post failed: %s", type(n).__name__, type(e).__name__)
        return ok


def from_settings(s: "Settings") -> Notifier:
    from fleetwatch.config import reveal
    from fleetwatch.notify.slack import SlackNotifier
    from fleetwatch.notify.teams import TeamsNotifier

    channels: list[Notifier] = []
    if reveal(s.slack_bot_token):
        channels.append(SlackNotifier(reveal(s.slack_bot_token), s.slack_channel))
    if s.teams_webhook_url and s.teams_webhook_url.get_secret_value():
        channels.append(TeamsNotifier(s.teams_webhook_url.get_secret_value()))
    return FanOut(channels) if channels else ConsoleNotifier(s.slack_channel)
