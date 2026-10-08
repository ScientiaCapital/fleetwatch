from datetime import UTC, datetime

from fleetwatch.epiphan.parse import apply_events, apply_recorder_status, apply_system_status, parse_devices
from tests.conftest import NOW


def test_devices_parse_from_real_shape(fleet):
    assert len(fleet.devices) == 31
    online = [d for d in fleet.devices.values() if d.online]
    assert len(online) == 20
    mini = next(d for d in fleet.devices.values() if d.name == "Room 312 Pearl Mini")
    assert mini.model == "Pearl Mini" and mini.group == "North Campus" and not mini.online
    assert "no_storage_detected" in mini.warnings
    assert set(mini.channels) == {"1", "2"} and mini.channels["1"].name == "Program"


def test_channel_warnings_and_recording(fleet):
    flagged = [
        (d.name, c.name)
        for d in fleet.devices.values()
        for c in d.channels.values()
        if "channel_no_signal" in c.warnings
    ]
    assert flagged, "fixture has at least one channel with no signal"
    assert any(d.recording for d in fleet.devices.values()), "fixture has a recording channel"


def test_no_ip_or_serial_leaks(device_list):
    text = str(device_list)
    assert "IPAddress" not in text and "SerialNumber" not in text


def test_parse_tolerates_junk():
    fleet = parse_devices({"devices": [None, {"Name": "no id"}, {"Id": "x", "Channels": ["bad", {"name": "P"}]}]}, NOW)
    assert list(fleet.devices) == ["x"] and fleet.devices["x"].channels["1"].name == "P"


def test_recorder_status_overrides(fleet):
    dev = next(d for d in fleet.devices.values() if d.online)
    cid = next(iter(dev.channels))
    apply_recorder_status(
        fleet,
        {dev.id: {"name": dev.name, "channels": {cid: {"name": "P", "recording_status": {"state": "recording"}}}}},
    )
    assert dev.channels[cid].recording
    apply_recorder_status(
        fleet, [{"device_id": dev.id, "channels": [{"channel_id": cid, "recording_status": {"state": "stopped"}}]}]
    )
    assert not dev.channels[cid].recording


def test_system_status_shapes(fleet):
    dev = next(d for d in fleet.devices.values() if d.online)
    apply_system_status(fleet, {dev.id: {"cpu": {"load": "95", "temperature": 61.5}, "uptime": 600}})
    s = fleet.system[dev.id]
    assert s.cpu_load_pct == 95 and s.cpu_temp_c == 61.5
    assert s.up_since is not None and (NOW - s.up_since).total_seconds() == 600


def test_events_shapes(fleet):
    dev = next(d for d in fleet.devices.values() if d.online)
    apply_events(
        fleet,
        {
            dev.id: {
                "event": {
                    "id": "e1",
                    "title": "BIO 101",
                    "start": "2026-10-07T15:20:00Z",
                    "end": "2026-10-07T16:10:00Z",
                }
            },
            "other": {"device_name": "x"},
        },
    )  # no `event` key means nothing scheduled
    assert set(fleet.events) == {dev.id}
    ev = fleet.events[dev.id]
    assert ev.title == "BIO 101" and ev.start == datetime(2026, 10, 7, 15, 20, tzinfo=UTC) and ev.key == f"{dev.id}:e1"


def test_stream_endpoints_keep_the_name_and_host_and_never_the_key():
    from fleetwatch.epiphan.parse import parse_stream_endpoints

    sid = "3f2a9c1e-5b7d-4e8f-9a0b-1c2d3e4f5a6b"
    url = "rtmp://rehearsal.example.invalid:1935/live/KEY123?token=abc"
    raw = {"stream_endpoints": [{"id": sid, "name": "Rehearsal stream", "url": url}]}
    (endpoint,) = parse_stream_endpoints(raw).values()
    assert (endpoint.id, endpoint.name, endpoint.host) == (sid, "Rehearsal stream", "rehearsal.example.invalid")
    assert "KEY123" not in repr(endpoint) and "token" not in repr(endpoint)


def test_stream_endpoints_that_cant_be_read_are_none_not_empty():
    from fleetwatch.epiphan.parse import parse_stream_endpoints

    assert parse_stream_endpoints("an error in words") is None
    assert parse_stream_endpoints({"stream_endpoints": [{"name": "no id"}]}) is None
    assert parse_stream_endpoints({"stream_endpoints": []}) == {}
