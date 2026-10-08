"""The redaction cases from the Edge Claude Kit's tests/hook-test.sh. None may leave FAKE in the output."""

import json
from pathlib import Path

import pytest

from fleetwatch.redact import MASK, redact, scrub_text

INNER = {
    "streams": [
        {"RTMP": {"StreamingKey": "FAKEKEY123", "URL": "rtmps://ingest.example.com:1936/meeting/FAKEPATH9"}},
        {"x": "srt://u:FAKEPW@h.example:9000?passphrase=FAKEPASS"},
    ],
    "settings": [{"id": "stream_key", "value": "FAKEID7"}],
}


@pytest.mark.parametrize("value", [INNER, json.dumps(INNER), [{"type": "text", "text": json.dumps(INNER)}]])
def test_keys_and_urls_masked_in_every_shape(value):
    out = json.dumps(redact(value))
    assert "FAKE" not in out
    assert "ingest.example.com:1936/" + MASK in out


@pytest.mark.parametrize(
    "text",
    [
        '{"url":"rtmp:\\/\\/a.example\\/live2\\/FAKE1"}',
        '{"data":"{\\"StreamingKey\\":\\"FAKE2\\"}"}',
        "{'stream_key': 'FAKE3'}",
        "Stream key: FAKE4",
        "password=FAKE5 and token: FAKE6",
        "https://x.example/live/FAKE7?token=FAKE8",
        "Authorization: Bearer FAKE9",
    ],
)
def test_more_hiding_places(text):
    assert "FAKE" not in json.dumps(redact(text))


def test_clean_output_untouched(device_list):
    assert redact(device_list) == device_list


def test_stream_id_uuid_kept_and_paging_kept():
    value = {"stream_id": "123e4567-e89b-12d3-a456-426614174000", "page_token": "abc", "stream_id_other": "secret"}
    out = redact(value)
    assert out["stream_id"] == value["stream_id"] and out["page_token"] == "abc"


def test_scrub_text_fast_path():
    assert scrub_text("Room 204 is offline") == "Room 204 is offline"


def test_teams_workflow_webhook_signature_is_masked():
    url = (
        "https://prod-00.westus.logic.example.com:443/workflows/0f0f/triggers/manual/paths/invoke"
        "?api-version=2016-06-01&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=FAKESIG123"
    )
    assert "FAKE" not in redact(f"posting to {url} failed")


@pytest.mark.parametrize(
    "text",
    ["stream key: live_9f8e7d6c", "password=hunter2; token: abc", "rtmp://a.example/live/x?k=1", '"secret": "s3"'],
)
def test_scrub_text_twice_is_the_same_as_once(text):
    # Room notes are redacted when saved and again when `ask` scrubs its answer; that must not leave "[redacted]]".
    once = scrub_text(text)
    assert MASK in once and scrub_text(once) == once


@pytest.mark.parametrize(
    "text",
    [
        "password=[redacted]hunter2",
        "password=[redacted][redacted]hunter2",
        "token: [redacted]abc",
        "https://live.example/[redacted]/secret",
        "rtmp://a.example/[redacted]live_key",
        "srt://a.example:9000?streamid=[redacted]x",
    ],
)
def test_a_typed_mask_cannot_hide_the_secret_after_it(text):
    # Someone could type "[redacted]" in a device name or a room note; the value after it must still be masked.
    out = scrub_text(text)
    for leak in ("hunter2", "abc", "/secret", "live_key", "]x"):
        assert leak not in out
    assert scrub_text(out) == out


# The shared corpus, byte-identical with the Epiphan Edge Claude Kit's copy. Don't edit it here; change both repos.
_CORPUS = json.loads((Path(__file__).parent / "redaction-cases.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _CORPUS, ids=[c["id"] for c in _CORPUS])
@pytest.mark.parametrize("fn", [scrub_text, redact], ids=["scrub_text", "redact"])
def test_shared_corpus(fn, case):
    once = fn(case["input"])
    for leak in case["must_not_contain"]:
        assert leak not in once
    for kept in case.get("must_contain", []):
        assert kept in once
    assert fn(once) == once


@pytest.mark.parametrize(
    "text",
    [
        "the monkey: George",
        "keyboard: US layout",
        "Keynote: opening session",
        "turkey=roast",
        "hotkey: F5",
        "Basically the room is fine",
        "a Bearer of good news",
        "| Name | Keynote speaker |\n|---|---|\n| Hall A | Dr. Example |",
        "| Room | Status |\n|---|---|\n| 204 | online |",
    ],
)
def test_words_that_only_look_like_key_names_are_kept(text):
    assert scrub_text(text) == text


@pytest.mark.parametrize(
    ("text", "leak"),
    [
        ("| Destination | Stream key | Status |\n|---|---|---|\n| YT | FAKE51 | live |", "FAKE51"),
        ("| Name | Password |\n| --- | :---: |\n| admin | FAKE52 |\n\nafter the table", "FAKE52"),
        ("| api-key | note |\n|---|---|\n| FAKE53 | x |", "FAKE53"),
        ('stream key = "FAKE WITH SPACES 54"', "SPACES"),
        ("stream%2Dkey=FAKE55", "FAKE55"),
        ("streaming key: FAKE56", "FAKE56"),
        ("private_key=FAKE57", "FAKE57"),
        ("X-API-Key: FAKE58", "FAKE58"),
        ("ws://u:FAKE59@ws.example/x", "FAKE59"),
        ('{"key": "FAKE60"}', "FAKE60"),
    ],
)
def test_new_secret_shapes(text, leak):
    for fn in (scrub_text, redact):
        out = fn(text)
        assert leak not in out and fn(out) == out


def test_table_keeps_other_columns_and_lines():
    text = "| Destination | Stream key |\n|---|---|\n| YouTube | FAKE61 |\nplain line"
    out = scrub_text(text)
    assert "YouTube" in out and "plain line" in out and "| Destination | Stream key |" in out and "FAKE61" not in out


def test_bearer_and_basic_keep_the_scheme_word():
    assert scrub_text("got Bearer FAKE62 back") == f"got Bearer {MASK} back"
    assert scrub_text("sent Basic RkFLRTYz=") == f"sent Basic {MASK}"


@pytest.mark.parametrize(
    "text",
    [
        "https://[redacted]u:FAKE64@host.example/x",
        "rtmp://[redacted]u:FAKE65@a.example/app",
        "ftp://[redacted]u:FAKE66@h/x",
    ],
)
def test_a_typed_mask_in_userinfo_cannot_hide_the_password(text):
    out = scrub_text(text)
    assert "FAKE" not in out and scrub_text(out) == out


def test_table_stream_id_column_keeps_uuids():
    uuid = "0f8fad5b-d9cb-469f-a165-70867728950e"
    out = scrub_text(f"| Stream ID | Name |\n|---|---|\n| {uuid} | A |\n| FAKE67 | B |")
    assert uuid in out and "FAKE67" not in out
