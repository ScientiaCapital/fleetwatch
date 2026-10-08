"""Microsoft Teams via a Power Automate Workflows webhook, and the fan-out that sends every post to each channel.
No real HTTP: urlopen is replaced in every test that posts."""

import json
import logging
import urllib.error

from pydantic import SecretStr

from fleetwatch.config import Settings
from fleetwatch.notify import ConsoleNotifier, FanOut, from_settings, teams
from fleetwatch.notify.slack import SlackNotifier
from fleetwatch.notify.teams import TeamsNotifier, teams_markdown

SIG = "FAKESIG0123456789abcdef"
URL = (
    "https://prod-00.westus.logic.example.com:443/workflows/0f0f0f0f/triggers/manual/paths/invoke"
    f"?api-version=2016-06-01&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig={SIG}"
)


class FakeResponse:
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def capture_urlopen(monkeypatch):
    sent = {}

    def urlopen(req, timeout=None):
        sent.update(url=req.full_url, data=json.loads(req.data), headers=dict(req.header_items()), timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr(teams.urllib.request, "urlopen", urlopen)
    return sent


def test_slack_mrkdwn_becomes_teams_markdown():
    text = "*Needs attention*\n• *Fix first*: Room 204 is offline. _Check power._"
    assert teams_markdown(text) == "**Needs attention**\n\n• **Fix first**: Room 204 is offline. _Check power._"


def test_untrusted_names_cannot_become_links():
    out = teams_markdown("• Offline (1): [click here](https://evil.example)")
    assert "](" not in out
    assert "click here" in out


def test_post_sends_one_wrapped_textblock_with_a_timeout(monkeypatch):
    sent = capture_urlopen(monkeypatch)
    assert TeamsNotifier(URL).post("*Nightly sweep* · 3 devices, 3 online")
    assert sent["url"] == URL
    assert sent["timeout"] == 10
    assert sent["headers"]["Content-type"] == "application/json"
    (attachment,) = sent["data"]["attachments"]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = attachment["content"]
    assert card["type"] == "AdaptiveCard"
    assert card["body"] == [{"type": "TextBlock", "text": "**Nightly sweep** · 3 devices, 3 online", "wrap": True}]


def test_failure_returns_false_and_never_logs_the_url(monkeypatch, caplog, capsys):
    def boom(req, timeout=None):
        raise urllib.error.URLError(f"cannot reach {URL}")

    monkeypatch.setattr(teams.urllib.request, "urlopen", boom)
    with caplog.at_level(logging.DEBUG):
        assert TeamsNotifier(URL).post("hello") is False
    out = caplog.text + capsys.readouterr().out
    assert "Teams post failed" in caplog.text
    assert SIG not in out and "logic.example.com" not in out


def test_http_error_status_is_logged_without_the_url(monkeypatch, caplog):
    def denied(req, timeout=None):
        raise urllib.error.HTTPError(URL, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(teams.urllib.request, "urlopen", denied)
    assert TeamsNotifier(URL).post("hello") is False
    assert "401" in caplog.text and SIG not in caplog.text


def test_repr_hides_the_url():
    assert SIG not in repr(TeamsNotifier(URL))


class Recorder:
    def __init__(self, ok: bool):
        self.ok, self.posts = ok, []

    def post(self, text: str) -> bool:
        self.posts.append(text)
        return self.ok


def test_fan_out_posts_to_every_channel_and_succeeds_if_any_did():
    a, b = Recorder(False), Recorder(True)
    assert FanOut([a, b]).post("hi")
    assert a.posts == b.posts == ["hi"]
    assert not FanOut([Recorder(False), Recorder(False)]).post("hi")


def test_fan_out_survives_a_channel_that_raises():
    class Broken:
        def post(self, text):
            raise RuntimeError("bug")

    ok = Recorder(True)
    assert FanOut([Broken(), ok]).post("hi")
    assert ok.posts == ["hi"]


def _settings(**kw) -> Settings:
    base = {"slack_bot_token": None, "teams_webhook_url": None}
    base.update(kw)
    return Settings(_env_file=None, **base)


def test_nothing_configured_prints_to_the_console(capsys):
    n = from_settings(_settings())
    assert isinstance(n, ConsoleNotifier)
    assert n.post("*Fleet check*")
    assert "*Fleet check*" in capsys.readouterr().out


def test_each_configured_channel_gets_the_post():
    n = from_settings(_settings(slack_bot_token="xoxb-test", teams_webhook_url=URL))
    assert isinstance(n, FanOut)
    assert sorted(type(c).__name__ for c in n.notifiers) == ["SlackNotifier", "TeamsNotifier"]
    only_teams = from_settings(_settings(teams_webhook_url=URL))
    assert [type(c) for c in only_teams.notifiers] == [TeamsNotifier]


def test_settings_keep_the_webhook_secret():
    s = _settings(teams_webhook_url=URL)
    assert isinstance(s.teams_webhook_url, SecretStr)
    assert SIG not in repr(s) and SIG not in str(s)


def test_slack_path_is_still_escaped():
    sent = {}

    class FakeClient:
        def chat_postMessage(self, **kw):
            sent.update(kw)

    n = SlackNotifier("xoxb-test", "#av-ops")
    n._client = FakeClient()
    assert n.post("<!here>")
    assert sent["text"] == "&lt;!here&gt;"
