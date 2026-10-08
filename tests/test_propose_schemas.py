"""The six reviewed argument schemas in the shipped tool_policy.yaml `propose` section, and `check_arguments`.

Validation only: nothing here can run a write, and guard() still refuses every write tool. The IDs are fake, in the
same shape as tests/fixtures (master `1f...`, channel `1f...-1`), never real ones.
"""

import json
from pathlib import Path

import pytest
import yaml

from fleetwatch import policy as policy_mod
from fleetwatch.epiphan.mcp import EpiphanClient, ToolNotAllowed
from fleetwatch.policy import check_arguments, load_tools
from fleetwatch.redact import redact

FIXTURES = Path(__file__).resolve().parent / "fixtures"

MASTER = "1f000000001"
MASTER_2 = "1f000000002"
CHANNEL = "1f000000001-1"
CHANNEL_2 = "1f000000002-2"
STREAM = "00000000-0000-4000-8000-0000000000a1"
EVENT = "demo-bio-101"

VALID = {
    "batch_recording": {"action": "start", "device_ids": [CHANNEL]},
    "start_stream_endpoint": {"stream_id": STREAM, "device_id": MASTER, "channel_id": "1"},
    "stop_stream_endpoint": {"stream_id": STREAM, "device_id": MASTER, "channel_id": "1"},
    "confirm_cms_event_on_device": {"device_id": MASTER, "event_id": EVENT},
    "batch_reboot": {"device_ids": [MASTER]},
    "batch_firmware_update": {"device_ids": [MASTER]},
}
TARGETS = {
    "batch_recording": (CHANNEL,),
    "start_stream_endpoint": (MASTER,),
    "stop_stream_endpoint": (MASTER,),
    "confirm_cms_event_on_device": (MASTER,),
    "batch_reboot": (MASTER,),
    "batch_firmware_update": (MASTER,),
}
# For each tool, an argument that names a device: a channel ID where a master is required, and the reverse.
WRONG_ID_KIND = {
    "batch_recording": {"device_ids": [MASTER]},
    "start_stream_endpoint": {"device_id": CHANNEL},
    "stop_stream_endpoint": {"device_id": CHANNEL},
    "confirm_cms_event_on_device": {"device_id": CHANNEL},
    "batch_reboot": {"device_ids": [CHANNEL]},
    "batch_firmware_update": {"device_ids": [CHANNEL]},
}
WRONG_TYPE = {
    "batch_recording": {"device_ids": CHANNEL},
    "start_stream_endpoint": {"channel_id": 1},
    "stop_stream_endpoint": {"device_id": 19000000001},
    "confirm_cms_event_on_device": {"event_id": ["demo"]},
    "batch_reboot": {"device_ids": [190000000001]},
    "batch_firmware_update": {"device_ids": {"id": MASTER}},
}
TWO_TARGETS = {
    "batch_recording": {"device_ids": [CHANNEL, CHANNEL_2]},
    "start_stream_endpoint": {"device_id": [MASTER, MASTER_2]},
    "stop_stream_endpoint": {"device_id": [MASTER, MASTER_2]},
    "confirm_cms_event_on_device": {"device_id": [MASTER, MASTER_2]},
    "batch_reboot": {"device_ids": [MASTER, MASTER_2]},
    "batch_firmware_update": {"device_ids": [MASTER, MASTER_2]},
}
TOOLS = sorted(VALID)


@pytest.fixture(scope="module")
def tp():
    return load_tools()


def check(tp, tool, args):
    return check_arguments(tool, tp.propose[tool], args)


# --- the shipped section ---------------------------------------------------------------------------------------------
def test_the_six_shipped_tools_all_have_reviewed_schemas(tp):
    assert set(tp.propose) == set(TOOLS)
    for tool in TOOLS:
        rule = tp.propose[tool]
        assert not rule.pending and tp.proposable(tool)
        assert rule.max_targets == 1, "one device per approval in v0.2"
        assert rule.schema.version == 1


def test_disruptive_flags_are_kept(tp):
    assert not tp.is_disruptive("start_stream_endpoint")
    assert not tp.is_disruptive("confirm_cms_event_on_device")
    for tool in ("stop_stream_endpoint", "batch_reboot", "batch_firmware_update"):
        assert tp.is_disruptive(tool)
    assert tp.is_disruptive("batch_recording", {"action": "stop"})
    assert not tp.is_disruptive("batch_recording", {"action": "start"})
    assert tp.is_disruptive("batch_recording"), "no arguments to look at: disruptive"


@pytest.mark.parametrize("tool", TOOLS)
def test_proposable_still_means_the_guard_refuses_it(tp, tool):
    client = EpiphanClient("https://example.invalid/mcp", tp, static_token="x")
    with pytest.raises(ToolNotAllowed):
        client.guard(tool)


def test_batch_recording_offers_start_and_stop_only(tp):
    assert tp.propose["batch_recording"].schema.fields["action"].values == ("start", "stop")


# --- check_arguments, per tool ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("tool", TOOLS)
def test_accepts_a_valid_example_and_returns_its_targets(tp, tool):
    assert check(tp, tool, VALID[tool]) == TARGETS[tool]


@pytest.mark.parametrize("tool", TOOLS)
def test_refuses_each_missing_field(tp, tool):
    for name in VALID[tool]:
        args = {k: v for k, v in VALID[tool].items() if k != name}
        with pytest.raises(ValueError, match=name):
            check(tp, tool, args)


@pytest.mark.parametrize("tool", TOOLS)
def test_refuses_a_wrong_type(tp, tool):
    with pytest.raises(ValueError):
        check(tp, tool, {**VALID[tool], **WRONG_TYPE[tool]})


@pytest.mark.parametrize("tool", TOOLS)
def test_refuses_an_extra_field(tp, tool):
    with pytest.raises(ValueError, match="force"):
        check(tp, tool, {**VALID[tool], "force": True})


@pytest.mark.parametrize("tool", TOOLS)
def test_refuses_the_wrong_kind_of_device_id(tp, tool):
    with pytest.raises(ValueError):
        check(tp, tool, {**VALID[tool], **WRONG_ID_KIND[tool]})


@pytest.mark.parametrize("tool", TOOLS)
def test_refuses_more_than_max_targets(tp, tool):
    with pytest.raises(ValueError):
        check(tp, tool, {**VALID[tool], **TWO_TARGETS[tool]})


@pytest.mark.parametrize("action", ["pause", "resume", "restart", "STOP", "", "stop "])
def test_batch_recording_refuses_any_other_action(tp, action):
    with pytest.raises(ValueError, match="action"):
        check(tp, "batch_recording", {**VALID["batch_recording"], "action": action})


def test_batch_recording_stop_is_accepted(tp):
    assert check(tp, "batch_recording", {"action": "stop", "device_ids": [CHANNEL]}) == (CHANNEL,)


@pytest.mark.parametrize("tool", ["batch_recording", "batch_reboot", "batch_firmware_update"])
def test_refuses_no_targets(tp, tool):
    with pytest.raises(ValueError):
        check(tp, tool, {**VALID[tool], "device_ids": []})


@pytest.mark.parametrize(
    "bad",
    [
        MASTER + "\n",  # `$` alone would let a trailing newline through
        CHANNEL + "\n",
        "../" + MASTER,
        MASTER.upper(),
        "1f00000001-1-1",
        CHANNEL + "0000",
        "Room 204 Pearl Mini",
        "",
    ],
)
def test_refuses_odd_device_ids(tp, bad):
    for tool in ("batch_reboot", "batch_recording"):
        with pytest.raises(ValueError):
            check(tp, tool, {**VALID[tool], "device_ids": [bad]})
    with pytest.raises(ValueError):
        check(tp, "confirm_cms_event_on_device", {**VALID["confirm_cms_event_on_device"], "device_id": bad})


@pytest.mark.parametrize("bad", ["0", "1a", "1000", "-1", " 1"])
def test_refuses_odd_channel_numbers(tp, bad):
    with pytest.raises(ValueError, match="channel_id"):
        check(tp, "start_stream_endpoint", {**VALID["start_stream_endpoint"], "channel_id": bad})


@pytest.mark.parametrize("bad", ["rtmp-key-abc123", "live_0123456789abcdef", "0000-0000", STREAM + "x"])
def test_stream_id_must_be_a_uuid(tp, bad):
    with pytest.raises(ValueError, match="stream_id"):
        check(tp, "start_stream_endpoint", {**VALID["start_stream_endpoint"], "stream_id": bad})


@pytest.mark.parametrize("tool", TOOLS)
def test_refuses_arguments_that_are_not_a_mapping(tp, tool):
    with pytest.raises(ValueError):
        check(tp, tool, [VALID[tool]])


def test_a_pending_rule_checks_nothing(tp):
    rule = policy_mod.ProposeRule(1, True, None)
    with pytest.raises(ValueError, match="pending"):
        check_arguments("batch_reboot", rule, VALID["batch_reboot"])


# --- the replay demo's devices fit the patterns ----------------------------------------------------------------------
def _fixture_devices():
    out = []
    for path in sorted(FIXTURES.rglob("get_devices_in_my_team.json")):
        for dev in json.loads(path.read_text())["devices"]:
            out.append((dev["Id"], [str(c["channel_id"]) for c in dev.get("Channels") or ()]))
    return out


def test_every_fixture_device_and_channel_can_be_proposed_against(tp):
    devices = _fixture_devices()
    assert devices
    for master, channels in devices:
        check(tp, "batch_reboot", {"device_ids": [master]})
        check(tp, "confirm_cms_event_on_device", {"device_id": master, "event_id": EVENT})
        for ch in channels:
            check(tp, "batch_recording", {"action": "start", "device_ids": [f"{master}-{ch}"]})
            check(tp, "start_stream_endpoint", {"stream_id": STREAM, "device_id": master, "channel_id": ch})


def test_fixture_event_ids_fit_the_event_pattern(tp):
    events = json.loads((FIXTURES / "get_current_or_next_cms_events_for_devices.json").read_text())
    ids = {m for m in _walk_ids(events)}
    assert ids
    for event_id in ids:
        check(tp, "confirm_cms_event_on_device", {"device_id": MASTER, "event_id": event_id})


def _walk_ids(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("id", "event_id") and isinstance(item, str) and item.startswith("demo-"):
                yield item
            else:
                yield from _walk_ids(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_ids(item)


# --- redaction and the approval card ---------------------------------------------------------------------------------
@pytest.mark.parametrize("tool", TOOLS)
def test_redact_leaves_every_valid_argument_as_it_is(tool):
    """The card shows every argument in full, so nothing a schema accepts may be masked by redact()."""
    assert redact(VALID[tool]) == VALID[tool]
    assert redact(json.dumps(VALID[tool])) == json.dumps(VALID[tool])


def test_a_plain_stream_id_is_kept_but_a_non_uuid_one_is_masked():
    assert redact({"stream_id": STREAM}) == {"stream_id": STREAM}
    assert redact({"stream_id": "live_0123456789abcdef"}) == {"stream_id": "[redacted]"}


# --- a narrowing file still can't widen the shipped schemas ----------------------------------------------------------
def _narrow(tmp_path: Path, change) -> Path:
    raw = yaml.safe_load(policy_mod._packaged_text())
    narrow = {"read": raw["read"], "propose": raw["propose"]}
    change(narrow["propose"])
    p = tmp_path / "narrow.yaml"
    p.write_text(yaml.safe_dump(narrow))
    return p


def test_a_narrowing_file_that_copies_the_shipped_section_loads(tmp_path):
    tp = load_tools(_narrow(tmp_path, lambda propose: None))
    assert all(tp.proposable(t) for t in TOOLS)


def _set(path: list, value):
    def change(propose):
        node = propose
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return change


WIDENINGS = {
    "loosen a master pattern": _set(
        ["batch_reboot", "schema", "fields", "device_ids", "items", "pattern"], "^[0-9a-f-]{1,64}$"
    ),
    "loosen a channel pattern": _set(["batch_recording", "schema", "fields", "device_ids", "items", "pattern"], ".+"),
    "add pause": _set(["batch_recording", "schema", "fields", "action", "values"], ["start", "stop", "pause"]),
    "make a field optional": _set(["start_stream_endpoint", "schema", "fields", "channel_id", "required"], False),
    "add a field": _set(["batch_reboot", "schema", "fields", "force"], {"type": "boolean"}),
    "raise max_targets": _set(["batch_firmware_update", "max_targets"], 2),
    "raise max_items": _set(["batch_firmware_update", "schema", "fields", "device_ids", "max_items"], 2),
    "turn disruptive off": _set(["batch_reboot", "disruptive"], False),
    "change the version": _set(["batch_reboot", "schema", "version"], 2),
}


@pytest.mark.parametrize("change", WIDENINGS.values(), ids=WIDENINGS.keys())
def test_a_narrowing_file_cannot_widen_a_shipped_schema(tmp_path, change):
    with pytest.raises(ValueError):
        load_tools(_narrow(tmp_path, change))
