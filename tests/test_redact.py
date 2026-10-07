"""The redaction cases from the Edge Claude Kit's tests/hook-test.sh. None may leave FAKE in the output."""

import json

import pytest

from proav_agent.redact import MASK, redact, scrub_text

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
