"""Post to Slack with slack_sdk. Never raises into the heartbeat."""

import logging

from fleetwatch.redact import redact

log = logging.getLogger(__name__)


def slack_escape(text: str) -> str:
    """Slack's three control characters. Our templates never use <links> or <@mentions>, so escaping the whole
    message is safe and stops a device named "<!channel>" from pinging everyone. *bold* and _italic_ still work."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class SlackNotifier:
    def __init__(self, token: str, channel: str):
        from slack_sdk import WebClient

        self.channel = channel
        self._web = WebClient(token=token)

    def post(self, text: str) -> bool:
        try:
            self._web.chat_postMessage(channel=self.channel, text=slack_escape(text), mrkdwn=True)
            return True
        except Exception as e:  # noqa: BLE001  (a failed post must not stop the loop)
            log.warning("Slack post failed: %s", redact(str(e)))
            return False
