"""Turning a real capture into a shareable fixture set (docs/replay.md). The input here is invented, shaped like a
live read; the real capture is never a test input and never leaves the machine it was made on."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from fleetwatch.epiphan.anonymize import AnonymizeError, anonymize, leaks, write_set
from fleetwatch.epiphan.parse import apply_events, apply_recorder_status, apply_system_status, parse_devices
from fleetwatch.epiphan.replay import resolve_relative_times

AT = datetime(2031, 5, 6, 14, 0, 0, tzinfo=UTC)  # the capture time; invented
ISO = "%Y-%m-%dT%H:%M:%SZ"

# Invented originals, each of which must disappear.
NAMES = ["Harbor Hall Annex", "Quarterly Town Hall", "Acme Booth Units", "Zed Cam Alpha", "ZX1234567"]
IDS = ["19aaa000001", "19aaa000002", "19aaa000003"]
EXTRA = ["10.20.30.40", "10.20.30.41", "9e1c4a52-0000-4000-8000-0000deadbeef"]


def _capture() -> dict:
    def ts(delta):
        return (AT + delta).strftime(ISO)

    devices = {
        "devices": [
            {
                "Id": IDS[0],
                "Name": NAMES[0],
                "Model": "Pearl Mini",
                "GroupId": EXTRA[2],
                "GroupName": NAMES[2],
                "IPAddress": EXTRA[0],
                "SerialNumber": NAMES[4],
                "Firmware": "4.24.6",
                "Status": "online",
                "StateTime": ts(-timedelta(hours=30)),
                "Channels": [
                    {
                        "channel_id": "1",
                        "name": NAMES[3],
                        "Publishers": [
                            {
                                "id": "0",
                                "type": "rtmp",
                                "status": {
                                    "since": 1790279331,
                                    "state": "stopped",
                                    "started": False,
                                    "description": "",
                                },
                                "settings": [
                                    {
                                        "id": "name",
                                        "type": {"name": "string"},
                                        "title": "Name",
                                        "value": "Stream for Harbor Hall Annex",
                                    }
                                ],
                            }
                        ],
                        "recording_status": {
                            "state": "stopped",
                            "timestamp": ts(-timedelta(hours=3)),
                            "description": "recording is not started",
                        },
                        "Warnings": [
                            {
                                "id": "channel_no_signal",
                                "data": {"channel_name": NAMES[3]},
                                "label": "nosignal",
                                "message": f"Channel '{NAMES[3]}' has no signal",
                            }
                        ],
                    }
                ],
                "Warnings": [
                    {
                        "id": "disk_space_error",
                        "data": None,
                        "label": "storage",
                        "message": "There is not enough free space",
                    }
                ],
            },
            {
                "Id": IDS[1],
                "Name": "Harbor Hall Lobby",
                "Model": "EC20",
                "GroupId": EXTRA[2],
                "GroupName": NAMES[2],
                "IPAddress": EXTRA[1],
                "SerialNumber": "ZX7654321",
                "Firmware": "4.24.5",
                "Status": "offline",
                "StateTime": ts(-timedelta(minutes=45)),
                "Channels": [],
                "Warnings": [],
            },
        ]
    }
    return {
        "get_devices_in_my_team": devices,
        "get_recorder_status_for_devices": {
            "devices": {
                IDS[0]: {
                    "Name": NAMES[0],
                    "Channels": {
                        "1": {
                            "Name": NAMES[3],
                            "RecordingStatus": {
                                "state": "stopped",
                                "timestamp": "1790279331",
                                "description": "recording is not started",
                            },
                        }
                    },
                }
            }
        },
        "get_current_or_next_cms_events_for_devices": {
            "events": [
                {
                    "device_id": IDS[0],
                    "event": {
                        "id": "da2soad3fmfs73ejsmhg:1791484740",
                        "title": NAMES[1],
                        "cms": "epiphan",
                        "status": "scheduled",
                        "confirmed": True,
                        "start": ts(timedelta(hours=3)),
                        "finish": ts(timedelta(hours=4)),
                        "devices": [
                            {
                                "device_id": IDS[0],
                                "channels": [{"channel_id": "1", "recording": True, "streaming": False}],
                            }
                        ],
                    },
                }
            ]
        },
        "get_system_status_for_devices": {
            "status": [
                {
                    "device_id": IDS[0],
                    "cpu_load_pct": 12,
                    "cpu_temp_c": 48,
                    "uptime_since": ts(-timedelta(hours=26, minutes=30)),
                }
            ]
        },
        "get_something_new": {"secret": NAMES[0]},  # a tool the whitelist doesn't know
    }


def test_nothing_from_the_original_survives():
    out = anonymize(_capture(), AT)
    text = json.dumps(out).lower()
    for original in NAMES + IDS + EXTRA + ["Harbor Hall Lobby", "ZX7654321", "Zed Cam"]:
        assert original.lower() not in text, original


def test_an_unknown_tool_is_dropped_not_copied():
    out = anonymize(_capture(), AT)
    assert "get_something_new" not in out


def test_ids_agree_across_every_file_and_look_like_device_ids():
    out = anonymize(_capture(), AT)
    dev_ids = {d["Id"] for d in out["get_devices_in_my_team"]["devices"]}
    assert len(dev_ids) == 2 and all(len(i) == 11 and set(i) <= set("0123456789abcdef") for i in dev_ids)
    assert set(out["get_recorder_status_for_devices"]["devices"]) <= dev_ids
    assert out["get_current_or_next_cms_events_for_devices"]["events"][0]["device_id"] in dev_ids
    assert out["get_system_status_for_devices"]["status"][0]["device_id"] in dev_ids


def test_names_addresses_and_serials_are_neutral_and_the_model_and_status_are_kept():
    first, second = anonymize(_capture(), AT)["get_devices_in_my_team"]["devices"]
    assert first["Name"].startswith("Room ") and first["Name"].endswith("Pearl Mini")
    assert second["Name"].endswith("EC20") and second["Status"] == "offline" and first["Model"] == "Pearl Mini"
    assert first["IPAddress"].startswith("192.0.2.") and first["SerialNumber"].startswith("SN")
    assert first["Firmware"] == "4.24.6" and first["GroupName"].startswith("Group ")


def test_warning_messages_are_rebuilt_from_neutral_names():
    dev = anonymize(_capture(), AT)["get_devices_in_my_team"]["devices"][0]
    channel = dev["Channels"][0]
    assert channel["Warnings"][0]["message"] == f"Channel '{channel['name']}' has no signal"
    assert channel["Warnings"][0]["data"]["channel_name"] == channel["name"]
    assert dev["Warnings"][0]["data"] is None, "the null that broke Epiphan's own schema is kept: it's real"


def test_times_become_relative_tokens_that_resolve_back_to_the_same_offsets():
    out = anonymize(_capture(), AT)
    text = json.dumps(out)
    assert "2031" not in text, "no absolute date survives"
    later = datetime(2040, 1, 1, 9, 0, 0, tzinfo=UTC)  # replay runs whenever
    raw = json.loads(resolve_relative_times(text, later))
    ev = raw["get_current_or_next_cms_events_for_devices"]["events"][0]["event"]
    assert ev["start"] == (later + timedelta(hours=3)).strftime(ISO)
    assert ev["finish"] == (later + timedelta(hours=4)).strftime(ISO)
    up = raw["get_system_status_for_devices"]["status"][0]["uptime_since"]
    assert up == (later - timedelta(hours=26, minutes=30)).strftime(ISO)


def test_the_result_still_parses_the_way_a_live_read_does():
    later = datetime(2040, 1, 1, 9, 0, 0, tzinfo=UTC)
    raw = json.loads(resolve_relative_times(json.dumps(anonymize(_capture(), AT)), later))
    fleet = parse_devices(raw["get_devices_in_my_team"], later)
    assert len(fleet.devices) == 2 and sum(d.online for d in fleet.devices.values()) == 1
    apply_recorder_status(fleet, raw["get_recorder_status_for_devices"])
    apply_events(fleet, raw["get_current_or_next_cms_events_for_devices"])
    apply_system_status(fleet, raw["get_system_status_for_devices"])
    assert len(fleet.events) == 1 and len(fleet.system) == 1
    (event,) = fleet.events.values()
    assert event.title.startswith("Sample ") and abs((event.start - later).total_seconds() - 3 * 3600) < 2


def test_it_is_deterministic():
    assert anonymize(_capture(), AT) == anonymize(_capture(), AT)


def test_leaks_finds_a_surviving_original_and_ignores_generic_words():
    assert leaks({"x": "Welcome to Harbor Hall Annex"}, ["Harbor Hall Annex"]) == ["Harbor Hall Annex"]
    assert leaks({"x": "Program"}, ["Program", "ab"]) == [], "short or generic strings aren't a signal"


def test_leaks_ignores_an_original_that_is_only_part_of_a_neutral_replacement_word():
    assert leaks({"x": "Camera 1"}, ["Camera"]) == [], "a channel called 'Camera' isn't a secret"
    assert leaks({"x": "Room 201 Pearl Mini"}, ["Pearl", "Room"]) == []
    assert leaks({"x": "Room 201 Pearl Mini, Harbor Hall"}, ["Harbor Hall"]) == ["Harbor Hall"]


def test_write_set_refuses_when_something_would_leak(tmp_path, monkeypatch):
    src = tmp_path / "in"
    src.mkdir()
    for tool, body in _capture().items():
        (src / f"{tool}.json").write_text(json.dumps(body))
    (src / "_manifest.json").write_text(json.dumps({"captured_at": AT.strftime(ISO), "tools": []}))
    dst = tmp_path / "out"
    report = write_set(src, dst)
    assert sorted(p.name for p in dst.glob("*.json")) == [
        "get_current_or_next_cms_events_for_devices.json",
        "get_devices_in_my_team.json",
        "get_recorder_status_for_devices.json",
        "get_system_status_for_devices.json",
    ]
    assert report["leaks"] == [] and "get_something_new" in report["dropped"]
    assert "_note" in json.loads((dst / "get_devices_in_my_team.json").read_text())

    import fleetwatch.epiphan.anonymize as mod

    monkeypatch.setattr(mod, "anonymize", lambda raw, at: {"get_devices_in_my_team": {"leak": NAMES[0]}})
    with pytest.raises(AnonymizeError):
        write_set(src, tmp_path / "out2")
    assert not (tmp_path / "out2").exists() or not list((tmp_path / "out2").glob("*.json"))
