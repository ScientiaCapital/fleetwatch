"""Per-action disruptive rules and a per-tool lead time in tool_policy.yaml's `propose` section (issue #100).

`disruptive_when: {action: [stop]}` names the values of one enum field that make a call disruptive. Anything else
(a missing field, an unknown value, no arguments at all) counts as disruptive. `lead_minutes` is the tool's own
readiness window. Configuration and validation only: nothing here can run a write.
"""

from pathlib import Path

import pytest

from fleetwatch import policy as policy_mod
from fleetwatch.policy import load_tool_policy, load_tools

READ = "read:\n  - get_devices_in_my_team\n  - get_recorder_status_for_devices\n"
WRITE = "write:\n  - batch_recording\n  - batch_reboot\n"
SCHEMA = """\
    schema:
      version: 1
      target: device_ids
      fields:
        device_ids:
          type: list
          required: true
          max_items: 2
          items: {type: string, pattern: "^[0-9a-f]{6,64}$"}
        action: {type: enum, required: true, values: [start, stop]}
"""
WHEN_STOP = "    disruptive_when: {action: [stop]}\n"


def entry(extra: str = "", disruptive: str = "true") -> str:
    return f"  batch_recording:\n    max_targets: 2\n    disruptive: {disruptive}\n{extra}" + SCHEMA


def policy_file(tmp_path: Path, propose: str, extra: str = "") -> Path:
    p = tmp_path / "tool_policy.yaml"
    p.write_text(READ + WRITE + extra + "propose:\n" + propose)
    return p


def narrow_file(tmp_path: Path, propose: str) -> Path:
    p = tmp_path / "narrow.yaml"
    p.write_text(READ + "propose:\n" + propose)
    return p


# --- the shipped file ------------------------------------------------------------------------------------------------
def test_shipped_batch_recording_start_is_calm_and_stop_is_disruptive():
    tp = load_tools()
    assert not tp.is_disruptive("batch_recording", {"action": "start", "device_ids": ["0a1b2c3d-1"]})
    assert tp.is_disruptive("batch_recording", {"action": "stop", "device_ids": ["0a1b2c3d-1"]})
    assert tp.is_disruptive("batch_recording"), "no arguments: the safe default"


def test_shipped_firmware_update_has_a_longer_window():
    tp = load_tools()
    assert tp.lead_minutes("batch_firmware_update", 30) == 120
    assert tp.lead_minutes("batch_reboot", 30) == 30, "no lead_minutes: the policy's own"
    assert tp.lead_minutes("not_a_tool", 45) == 45


# --- is_disruptive ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("args", [None, {}, {"action": "pause"}, {"action": ["start"]}, {"action": 1}, "start"])
def test_unknown_missing_or_unreadable_action_is_disruptive(tmp_path, args):
    tp = load_tool_policy(policy_file(tmp_path, entry(WHEN_STOP)))
    assert tp.is_disruptive("batch_recording", args)


def test_disruptive_when_names_only_the_disruptive_values(tmp_path):
    tp = load_tool_policy(policy_file(tmp_path, entry(WHEN_STOP)))
    assert not tp.is_disruptive("batch_recording", {"action": "start"})
    assert tp.is_disruptive("batch_recording", {"action": "stop"})
    assert tp.propose["batch_recording"].disruptive_when == ("action", frozenset({"stop"}))


def test_without_disruptive_when_every_call_is_disruptive(tmp_path):
    tp = load_tool_policy(policy_file(tmp_path, entry()))
    assert tp.is_disruptive("batch_recording", {"action": "start"})


def test_a_tool_on_the_disruptive_list_stays_disruptive_whatever_the_arguments(tmp_path):
    tp = load_tool_policy(policy_file(tmp_path, entry(), extra="disruptive:\n  - batch_recording\n"))
    assert tp.is_disruptive("batch_recording", {"action": "start"})


# --- validation ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "extra",
    [
        "    disruptive_when: [stop]\n",  # not a mapping
        "    disruptive_when: {}\n",  # empty
        "    disruptive_when: {action: stop}\n",  # values not a list
        "    disruptive_when: {action: []}\n",  # empty list
        "    disruptive_when: {action: [stop, pause]}\n",  # value not in the enum
        "    disruptive_when: {nope: [stop]}\n",  # field not in the schema
        "    disruptive_when: {device_ids: [stop]}\n",  # not an enum field
        "    disruptive_when: {action: [stop], device_ids: [x]}\n",  # more than one field
        "    disruptive_when: {action: [1]}\n",  # not text
    ],
)
def test_refuses_a_malformed_disruptive_when(tmp_path, extra):
    with pytest.raises(ValueError, match="disruptive_when"):
        load_tool_policy(policy_file(tmp_path, entry(extra)))


def test_refuses_disruptive_when_on_a_tool_that_says_disruptive_false(tmp_path):
    with pytest.raises(ValueError, match="disruptive_when"):
        load_tool_policy(policy_file(tmp_path, entry(WHEN_STOP, disruptive="false")))


def test_refuses_disruptive_when_on_a_pending_schema(tmp_path):
    pending = f"  batch_recording:\n    max_targets: 1\n{WHEN_STOP}    schema: pending-live-run\n"
    with pytest.raises(ValueError, match="disruptive_when"):
        load_tool_policy(policy_file(tmp_path, pending))


def test_refuses_disruptive_when_on_a_tool_on_the_disruptive_list(tmp_path):
    p = policy_file(tmp_path, entry(WHEN_STOP), extra="disruptive:\n  - batch_recording\n")
    with pytest.raises(ValueError, match="disruptive_when"):
        load_tool_policy(p)


@pytest.mark.parametrize("value", ["0", "-5", "true", "'30'", "1.5", "2000"])
def test_refuses_a_bad_lead_minutes(tmp_path, value):
    with pytest.raises(ValueError, match="lead_minutes"):
        load_tool_policy(policy_file(tmp_path, entry(f"    lead_minutes: {value}\n")))


def test_lead_minutes_loads(tmp_path):
    tp = load_tool_policy(policy_file(tmp_path, entry("    lead_minutes: 90\n")))
    assert tp.lead_minutes("batch_recording", 30) == 90


# --- FLEETWATCH_TOOL_POLICY_FILE can only make things stricter -------------------------------------------------------
def package(monkeypatch, propose: str) -> None:
    text = READ + WRITE + "propose:\n" + propose
    monkeypatch.setattr(policy_mod, "_packaged_text", lambda: text)


def test_narrowing_file_may_keep_or_widen_what_is_disruptive(monkeypatch, tmp_path):
    package(monkeypatch, entry(WHEN_STOP + "    lead_minutes: 120\n"))
    assert load_tools(narrow_file(tmp_path, entry(WHEN_STOP + "    lead_minutes: 120\n")))
    rule = load_tools(narrow_file(tmp_path, entry("    lead_minutes: 180\n"))).propose["batch_recording"]
    assert rule.disruptive_when is None and rule.lead_minutes == 180
    assert load_tools(
        narrow_file(tmp_path, entry("    disruptive_when: {action: [start, stop]}\n    lead_minutes: 120\n"))
    )


def test_narrowing_file_cannot_shrink_the_disruptive_set(monkeypatch, tmp_path):
    package(monkeypatch, entry("    disruptive_when: {action: [start, stop]}\n"))
    with pytest.raises(ValueError, match="disruptive_when"):
        load_tools(narrow_file(tmp_path, entry(WHEN_STOP)))


def test_narrowing_file_cannot_add_disruptive_when_the_package_lacks(monkeypatch, tmp_path):
    package(monkeypatch, entry())
    with pytest.raises(ValueError, match="disruptive_when"):
        load_tools(narrow_file(tmp_path, entry(WHEN_STOP)))


@pytest.mark.parametrize("extra", ["", "    lead_minutes: 60\n"])
def test_narrowing_file_cannot_shorten_the_window(monkeypatch, tmp_path, extra):
    package(monkeypatch, entry(WHEN_STOP + "    lead_minutes: 120\n"))
    with pytest.raises(ValueError, match="lead_minutes"):
        load_tools(narrow_file(tmp_path, entry(WHEN_STOP + extra)))


# --- stops_recording: the action that ends a recording, so "the room is recording" doesn't block it -----------------
STOPS = "    stops_recording: {action: [stop]}\n"


def test_shipped_stop_ends_a_recording_and_nothing_else_does():
    tp = load_tools()
    assert tp.stops_recording("batch_recording", {"action": "stop", "device_ids": ["0a1b2c3d-1"]})
    assert not tp.stops_recording("batch_recording", {"action": "start"})
    assert not tp.stops_recording("batch_recording", {"action": "pause"})
    assert not tp.stops_recording("batch_recording")
    assert not tp.stops_recording("batch_reboot", {"action": "stop"})
    assert not tp.stops_recording("batch_firmware_update", {})


def test_stops_recording_loads(tmp_path):
    tp = load_tool_policy(policy_file(tmp_path, entry(WHEN_STOP + STOPS)))
    assert tp.stops_recording("batch_recording", {"action": "stop"})
    assert not tp.stops_recording("batch_recording", {"action": ["stop"]})


@pytest.mark.parametrize(
    "extra",
    [
        "    stops_recording: [stop]\n",
        "    stops_recording: {action: []}\n",
        "    stops_recording: {action: [pause]}\n",
        "    stops_recording: {nope: [stop]}\n",
    ],
)
def test_refuses_a_malformed_stops_recording(tmp_path, extra):
    with pytest.raises(ValueError, match="stops_recording"):
        load_tool_policy(policy_file(tmp_path, entry(WHEN_STOP + extra)))


def test_narrowing_file_cannot_add_or_widen_stops_recording(monkeypatch, tmp_path):
    package(monkeypatch, entry(WHEN_STOP))
    with pytest.raises(ValueError, match="stops_recording"):
        load_tools(narrow_file(tmp_path, entry(WHEN_STOP + STOPS)))
    package(monkeypatch, entry(WHEN_STOP + STOPS))
    assert load_tools(narrow_file(tmp_path, entry(WHEN_STOP)))  # dropping it is stricter
