"""The redaction cases from the Edge Claude Kit's tests/hook-test.sh. None may leave FAKE in the output."""

import json

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
