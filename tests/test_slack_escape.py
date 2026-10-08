"""Device, channel and event names are untrusted: in Slack they must not become mentions or links."""

from fleetwatch.notify import ConsoleNotifier
from fleetwatch.notify.slack import SlackNotifier, slack_escape


def test_control_characters_are_escaped_and_formatting_survives():
    assert slack_escape("*Fix first*: <!channel> & <@U123> <https://evil.example|click>") == (
        "*Fix first*: &lt;!channel&gt; &amp; &lt;@U123&gt; &lt;https://evil.example|click&gt;"
    )
    assert slack_escape("_Check power._ • Room 204") == "_Check power._ • Room 204"


def test_slack_posts_are_escaped_and_the_console_is_not(capsys):
    sent = {}

    class FakeClient:
        def chat_postMessage(self, **kw):
            sent.update(kw)

    n = SlackNotifier("xoxb-test", "#av-ops")
    n._web = FakeClient()
    assert n.post("*Nightly sweep* · Offline (1): <!here> Lobby")
    assert sent["text"] == "*Nightly sweep* · Offline (1): &lt;!here&gt; Lobby"
    assert sent.get("link_names") in (None, False, 0)

    ConsoleNotifier("#av-ops").post("Lobby <b>")
    assert "Lobby <b>" in capsys.readouterr().out


def test_console_output_says_it_is_a_preview(capsys) -> None:
    """A booth screen shows the console notifier; its header must not read like a real Slack post."""
    assert ConsoleNotifier("#av-ops").post("*Lobby Pearl Mini* is offline")
    out = capsys.readouterr().out
    assert "[console preview for #av-ops]" in out
    assert "*Lobby Pearl Mini* is offline" in out


def test_console_output_with_no_channel_has_no_dangling_for(capsys) -> None:
    assert ConsoleNotifier("").post("All clear.")
    out = capsys.readouterr().out
    assert "[console preview]" in out
    assert "for" not in out.split("\n")[1]
