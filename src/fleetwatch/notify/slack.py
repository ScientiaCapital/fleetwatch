"""Post to Slack with slack_sdk, or print when no token is configured. Never raises into the heartbeat."""

import logging

log = logging.getLogger(__name__)


def slack_escape(text: str) -> str:
    """Slack's three control characters. Our templates never use <links> or <@mentions>, so escaping the whole
    message is safe and stops a device named "<!channel>" from pinging everyone. *bold* and _italic_ still work."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Notifier:
    def __init__(self, token: str | None, channel: str):
        self.channel = channel
        self._client = None
        if token:
            from slack_sdk import WebClient

            self._client = WebClient(token=token)

    def post(self, text: str) -> bool:
        if self._client is None:
            print(f"\n[{self.channel}]\n{text}\n")
            return True
        try:
            self._client.chat_postMessage(channel=self.channel, text=slack_escape(text), mrkdwn=True)
            return True
        except Exception as e:  # noqa: BLE001  (a failed post must not stop the loop)
            log.warning("Slack post failed: %s", e)
            return False
