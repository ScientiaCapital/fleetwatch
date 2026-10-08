"""Post to Microsoft Teams through a Power Automate "Workflows" webhook, as an Adaptive Card with one TextBlock.

The webhook URL carries its own signature (`sig=`), so anyone holding it can post to the channel. It is kept out of
every log line, error and repr: failures log only the error type or HTTP status. Never raises into the heartbeat.
"""

import json
import logging
import re
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

TIMEOUT_S = 10
_BOLD = re.compile(r"(?<![*\w])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![*\w])")


def teams_markdown(text: str) -> str:
    """Slack mrkdwn to the Markdown subset a Teams TextBlock renders.

    - `*bold*` becomes `**bold**` (a single `*` is italic in Teams).
    - `_italic_` is the same in both, so it stays.
    - `•` bullets stay as text: a TextBlock's `- ` lists need every item on adjacent lines and misrender next to
      headings, while a literal bullet always looks the same.
    - Teams folds single newlines into spaces, so each line becomes its own paragraph.
    - `](` is broken up so a device named `[click](https://...)` shows as text instead of a link.
    """
    text = text.replace("](", "] (")
    text = _BOLD.sub(r"**\1**", text)
    return "\n\n".join(text.split("\n"))


def card(text: str) -> dict:
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [{"type": "TextBlock", "text": teams_markdown(text), "wrap": True}],
                },
            }
        ],
    }


class TeamsNotifier:
    def __init__(self, webhook_url: str):
        self._url = webhook_url

    def __repr__(self) -> str:
        return "TeamsNotifier(webhook=[redacted])"

    def post(self, text: str) -> bool:
        body = json.dumps(card(text)).encode()
        req = urllib.request.Request(self._url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                status = getattr(resp, "status", 200)
            if 200 <= status < 300:
                return True
            log.warning("Teams post failed: HTTP %s", status)
        except urllib.error.HTTPError as e:
            log.warning("Teams post failed: HTTP %s", e.code)
        except Exception as e:  # noqa: BLE001  (a failed post must not stop the loop; str(e) may hold the URL)
            log.warning("Teams post failed: %s", type(e).__name__)
        return False
