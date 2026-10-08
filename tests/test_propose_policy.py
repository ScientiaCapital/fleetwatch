"""The `propose` section of tool_policy.yaml (v0.2 design, docs/design/approved-writes.md) and `autonomy: propose`.

Configuration and validation only: nothing here can run a write. Loading refuses anything that would let a tool be
proposed that isn't a reviewed write tool with a well-formed argument schema.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from fleetwatch import policy as policy_mod
from fleetwatch.epiphan.mcp import EpiphanClient, ToolNotAllowed
from fleetwatch.policy import KNOWN_WRITE_TOOLS, load_policy, load_tool_policy, load_tools

ROOT = Path(__file__).resolve().parents[1]

READ = "read:\n  - get_devices_in_my_team\n  - get_recorder_status_for_devices\n"
WRITE = "write:\n  - batch_recording\n  - batch_reboot\n  - start_stream_endpoint\n"

# A complete, reviewed schema: the shape every non-pending `propose` entry must have.
GOOD_SCHEMA = """\
    schema:
      version: 1
      target: device_ids
      fields:
        device_ids:
          type: list
          required: true
          max_items: 2
          items:
            type: string
            pattern: "^[0-9a-f]{6,64}$"
        action:
          type: enum
          required: true
          values: [start, stop]
"""


def good_entry(tool: str = "batch_recording", max_targets: str = "2", disruptive: str = "false") -> str:
    return f"  {tool}:\n    max_targets: {max_targets}\n    disruptive: {disruptive}\n" + GOOD_SCHEMA


def write_policy(tmp_path: Path, propose: str, read: str = READ, write: str = WRITE, extra: str = "") -> Path:
    p = tmp_path / "tool_policy.yaml"
    p.write_text(read + write + extra + "propose:\n" + propose)
    return p


# --- the shipped file ------------------------------------------------------------------------------------------------
def test_shipped_propose_section_loads_and_only_names_write_tools():
    tp = load_tool_policy(ROOT / "tool_policy.yaml")
    assert tp.propose, "the shipped file describes the tools v0.2 may propose"
    assert set(tp.propose) <= tp.write
    assert not set(tp.propose) & tp.read


def test_shipped_schemas_are_reviewed_and_a_pending_one_would_not_be_proposable():
    tp = load_tools()
    assert not [t for t, rule in tp.propose.items() if rule.pending], "schemas come from the tool definitions"
    tool = "batch_reboot"
    pending = policy_mod.ToolPolicy(tp.read, tp.write, tp.disruptive, {tool: replace(tp.propose[tool], schema=None)})
    assert not pending.proposable(tool)
    assert pending.is_disruptive(tool), "a tool that hasn't been reviewed counts as disruptive"


def test_unreviewed_tools_are_on_the_disruptive_list():
    tp = load_tools()
    for tool in ("switch_device_to_cms", "update_cms_event", "cms_event_action"):
        assert tool in tp.disruptive
    assert "batch_recording" not in tp.disruptive, "its propose entry says which actions are disruptive"


def test_disruptive_list_only_names_write_tools():
    tp = load_tools()
    assert tp.disruptive <= tp.write


def test_read_list_is_unchanged():
    tp = load_tools()
    assert tp.read == frozenset(
        {
            "get_devices_in_my_team",
            "get_device_info",
            "get_device_sources",
            "get_system_status_for_devices",
            "get_recorder_status_for_devices",
            "get_storage_status_for_devices",
            "get_channel_settings",
            "get_channel_image",
            "get_channel_audio_levels",
            "get_stream_endpoint",
            "get_stream_endpoints",
            "get_team_presets",
            "get_cms_events_for_device",
            "get_cms_events_for_devices",
            "get_current_or_next_cms_event_for_device",
            "get_current_or_next_cms_events_for_devices",
            "get_cms_names_for_devices",
            "get_devices_by_cms",
            "kb_search",
            "kb_fetch",
        }
    )
    assert not tp.read & KNOWN_WRITE_TOOLS


# --- a reviewed schema -----------------------------------------------------------------------------------------------
def test_a_reviewed_schema_is_proposable(tmp_path):
    tp = load_tool_policy(write_policy(tmp_path, good_entry()))
    rule = tp.propose["batch_recording"]
    assert not rule.pending and rule.max_targets == 2 and rule.disruptive is False
    assert rule.schema.target == "device_ids"
    assert rule.schema.fields["device_ids"].required
    assert tp.proposable("batch_recording")
    assert not tp.is_disruptive("batch_recording")


def test_tools_not_listed_are_not_proposable_and_count_as_disruptive(tmp_path):
    tp = load_tool_policy(write_policy(tmp_path, good_entry()))
    for tool in ("batch_reboot", "start_stream_endpoint", "get_devices_in_my_team", "some_future_tool"):
        assert not tp.proposable(tool)
    assert tp.is_disruptive("start_stream_endpoint")


def test_on_the_disruptive_list_wins_over_the_entry(tmp_path):
    p = write_policy(tmp_path, good_entry(disruptive="true"), extra="disruptive:\n  - batch_recording\n")
    assert load_tool_policy(p).is_disruptive("batch_recording")


def test_disruptive_defaults_to_true_when_left_out(tmp_path):
    entry = "  batch_recording:\n    max_targets: 2\n" + GOOD_SCHEMA
    tp = load_tool_policy(write_policy(tmp_path, entry))
    assert tp.propose["batch_recording"].disruptive is True


def test_pending_schema_loads_but_is_not_proposable(tmp_path):
    entry = "  batch_recording:\n    max_targets: 1\n    disruptive: false\n    schema: pending-live-run\n"
    tp = load_tool_policy(write_policy(tmp_path, entry))
    assert tp.propose["batch_recording"].pending
    assert not tp.proposable("batch_recording")
    assert tp.is_disruptive("batch_recording"), "pending means not reviewed, so disruptive"


# --- loader refusals -------------------------------------------------------------------------------------------------
def test_refuses_a_propose_tool_that_is_not_a_write_tool(tmp_path):
    p = write_policy(tmp_path, good_entry(tool="apply_team_preset"))
    with pytest.raises(ValueError, match="apply_team_preset"):
        load_tool_policy(p)


def test_refuses_a_propose_tool_that_is_a_read_tool(tmp_path):
    p = write_policy(tmp_path, good_entry(tool="get_devices_in_my_team"))
    with pytest.raises(ValueError, match="get_devices_in_my_team"):
        load_tool_policy(p)


def test_refuses_a_propose_tool_on_both_read_and_write(tmp_path):
    p = write_policy(tmp_path, good_entry(tool="get_device_info"), read=READ + "  - get_device_info\n",
                     write=WRITE + "  - get_device_info\n")  # fmt: skip
    with pytest.raises(ValueError, match="get_device_info"):
        load_tool_policy(p)


def test_refuses_missing_max_targets(tmp_path):
    entry = "  batch_recording:\n    disruptive: false\n" + GOOD_SCHEMA
    with pytest.raises(ValueError, match="max_targets"):
        load_tool_policy(write_policy(tmp_path, entry))


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "two", "true", "null", "[1]"])
def test_refuses_bad_max_targets(tmp_path, value):
    with pytest.raises(ValueError, match="max_targets"):
        load_tool_policy(write_policy(tmp_path, good_entry(max_targets=value)))


@pytest.mark.parametrize("value", ["yes-please", "1", "null", "'true'"])
def test_refuses_a_disruptive_flag_that_is_not_true_or_false(tmp_path, value):
    with pytest.raises(ValueError, match="disruptive"):
        load_tool_policy(write_policy(tmp_path, good_entry(disruptive=value)))


def test_refuses_disruptive_false_for_a_tool_on_the_disruptive_list(tmp_path):
    p = write_policy(tmp_path, good_entry(disruptive="false"), extra="disruptive:\n  - batch_recording\n")
    with pytest.raises(ValueError, match="batch_recording"):
        load_tool_policy(p)


def test_refuses_a_disruptive_entry_that_is_not_a_write_tool(tmp_path):
    p = write_policy(tmp_path, good_entry(), extra="disruptive:\n  - get_devices_in_my_team\n")
    with pytest.raises(ValueError, match="get_devices_in_my_team"):
        load_tool_policy(p)


def test_refuses_an_unknown_key_in_an_entry(tmp_path):
    entry = good_entry() + "    auto_approve: true\n"
    with pytest.raises(ValueError, match="auto_approve"):
        load_tool_policy(write_policy(tmp_path, entry))


def test_refuses_a_propose_section_that_is_not_a_mapping(tmp_path):
    with pytest.raises(ValueError, match="propose"):
        load_tool_policy(write_policy(tmp_path, "  - batch_recording\n"))


def test_refuses_an_entry_with_no_schema(tmp_path):
    entry = "  batch_recording:\n    max_targets: 1\n    disruptive: false\n"
    with pytest.raises(ValueError, match="schema"):
        load_tool_policy(write_policy(tmp_path, entry))


MALFORMED = {
    "a string that is not the pending marker": "    schema: pending\n",
    "no fields": "    schema:\n      version: 1\n      target: device_ids\n",
    "empty fields": "    schema:\n      version: 1\n      target: device_ids\n      fields: {}\n",
    "no version": (
        "    schema:\n      target: device_ids\n      fields:\n        device_ids:\n          type: string\n"
        "          required: true\n          pattern: '^[0-9a-f]+$'\n"
    ),
    "no target": (
        "    schema:\n      version: 1\n      fields:\n        device_ids:\n          type: string\n"
        "          required: true\n          pattern: '^[0-9a-f]+$'\n"
    ),
    "target not a field": (
        "    schema:\n      version: 1\n      target: room\n      fields:\n        device_ids:\n          type: string\n"
        "          required: true\n          pattern: '^[0-9a-f]+$'\n"
    ),
    "target not required": (
        "    schema:\n      version: 1\n      target: device_ids\n      fields:\n        device_ids:\n"
        "          type: string\n          required: false\n          pattern: '^[0-9a-f]+$'\n"
    ),
    "target with no ID format": (
        "    schema:\n      version: 1\n      target: device_ids\n      fields:\n        device_ids:\n"
        "          type: string\n          required: true\n"
    ),
    "target that is a number": (
        "    schema:\n      version: 1\n      target: device_ids\n      fields:\n        device_ids:\n"
        "          type: integer\n          required: true\n"
    ),
    "unknown type": (
        "    schema:\n      version: 1\n      target: device_ids\n      fields:\n        device_ids:\n"
        "          type: string\n          required: true\n          pattern: '^[0-9a-f]+$'\n"
        "        script:\n          type: code\n          required: false\n"
    ),
    "required not a boolean": (
        "    schema:\n      version: 1\n      target: device_ids\n      fields:\n        device_ids:\n"
        "          type: string\n          required: 'yes'\n          pattern: '^[0-9a-f]+$'\n"
    ),
    "regex that does not compile": (
        "    schema:\n      version: 1\n      target: device_ids\n      fields:\n        device_ids:\n"
        "          type: string\n          required: true\n          pattern: '^[0-9a-f+$'\n"
    ),
    "enum with no values": (
        "    schema:\n      version: 1\n      target: device_ids\n      fields:\n        device_ids:\n"
        "          type: string\n          required: true\n          pattern: '^[0-9a-f]+$'\n"
        "        action:\n          type: enum\n          required: true\n          values: []\n"
    ),
    "list with no items": (
        "    schema:\n      version: 1\n      target: device_ids\n      fields:\n        device_ids:\n"
        "          type: list\n          required: true\n"
    ),
    "list of lists": (
        "    schema:\n      version: 1\n      target: device_ids\n      fields:\n        device_ids:\n"
        "          type: list\n          required: true\n          items:\n            type: list\n"
        "            items:\n              type: string\n              pattern: '^[0-9a-f]+$'\n"
    ),
    "unknown key on a field": (
        "    schema:\n      version: 1\n      target: device_ids\n      fields:\n        device_ids:\n"
        "          type: string\n          required: true\n          pattern: '^[0-9a-f]+$'\n          default_all: true\n"
    ),
    "unknown key on the schema": (
        "    schema:\n      version: 1\n      target: device_ids\n      allow_extra: true\n      fields:\n"
        "        device_ids:\n          type: string\n          required: true\n          pattern: '^[0-9a-f]+$'\n"
    ),
    "field name that is not an identifier": (
        "    schema:\n      version: 1\n      target: device_ids\n      fields:\n        device_ids:\n"
        "          type: string\n          required: true\n          pattern: '^[0-9a-f]+$'\n"
        "        '../x':\n          type: string\n          required: false\n"
    ),
    "max_items larger than max_targets": (
        "    schema:\n      version: 1\n      target: device_ids\n      fields:\n        device_ids:\n"
        "          type: list\n          required: true\n          max_items: 50\n          items:\n"
        "            type: string\n            pattern: '^[0-9a-f]+$'\n"
    ),
}


@pytest.mark.parametrize("schema", MALFORMED.values(), ids=MALFORMED.keys())
def test_refuses_a_malformed_schema(tmp_path, schema):
    entry = "  batch_recording:\n    max_targets: 2\n    disruptive: false\n" + schema
    with pytest.raises(ValueError, match="batch_recording"):
        load_tool_policy(write_policy(tmp_path, entry))


# --- FLEETWATCH_TOOL_POLICY_FILE: may remove propose tools, never add or widen one ---------------------------------
@pytest.fixture
def packaged_with_a_reviewed_tool(monkeypatch):
    text = (
        READ
        + WRITE
        + "propose:\n"
        + good_entry(max_targets="2")
        + ("  batch_reboot:\n    max_targets: 1\n    disruptive: true\n    schema: pending-live-run\n")
    )
    monkeypatch.setattr(policy_mod, "_packaged_text", lambda: text)


def narrow_file(tmp_path: Path, propose: str | None) -> Path:
    p = tmp_path / "narrow.yaml"
    p.write_text(READ + ("" if propose is None else "propose:\n" + propose))
    return p


def test_narrowing_file_without_propose_removes_every_propose_tool(tmp_path, packaged_with_a_reviewed_tool):
    tp = load_tools(narrow_file(tmp_path, None))
    assert not tp.propose and not tp.proposable("batch_recording")
    assert tp.write == load_tools().write, "write and disruptive still come from the package"


def test_narrowing_file_may_remove_a_propose_tool(tmp_path, packaged_with_a_reviewed_tool):
    tp = load_tools(narrow_file(tmp_path, good_entry()))
    assert set(tp.propose) == {"batch_recording"} and tp.proposable("batch_recording")


def test_narrowing_file_may_lower_max_targets_and_make_a_tool_disruptive(tmp_path, packaged_with_a_reviewed_tool):
    entry = good_entry(max_targets="1", disruptive="true").replace("max_items: 2", "max_items: 1")
    rule = load_tools(narrow_file(tmp_path, entry)).propose["batch_recording"]
    assert rule.max_targets == 1 and rule.disruptive is True


def test_narrowing_file_may_set_a_schema_back_to_pending(tmp_path, packaged_with_a_reviewed_tool):
    entry = "  batch_recording:\n    max_targets: 2\n    disruptive: false\n    schema: pending-live-run\n"
    tp = load_tools(narrow_file(tmp_path, entry))
    assert not tp.proposable("batch_recording")


def test_narrowing_file_cannot_add_a_propose_tool(tmp_path, packaged_with_a_reviewed_tool):
    with pytest.raises(ValueError, match="start_stream_endpoint"):
        load_tools(narrow_file(tmp_path, good_entry(tool="start_stream_endpoint")))


def test_narrowing_file_cannot_raise_max_targets(tmp_path, packaged_with_a_reviewed_tool):
    with pytest.raises(ValueError, match="max_targets"):
        load_tools(narrow_file(tmp_path, good_entry(max_targets="5")))


def test_narrowing_file_cannot_make_a_disruptive_tool_calm(tmp_path, packaged_with_a_reviewed_tool):
    entry = "  batch_reboot:\n    max_targets: 1\n    disruptive: false\n    schema: pending-live-run\n"
    with pytest.raises(ValueError, match="disruptive"):
        load_tools(narrow_file(tmp_path, entry))


def test_narrowing_file_cannot_give_a_pending_tool_a_schema(tmp_path, packaged_with_a_reviewed_tool):
    entry = good_entry(tool="batch_reboot", max_targets="1", disruptive="true").replace("max_items: 2", "max_items: 1")
    with pytest.raises(ValueError, match="schema"):
        load_tools(narrow_file(tmp_path, entry))


def test_narrowing_file_cannot_raise_the_target_max_items(tmp_path, packaged_with_a_reviewed_tool):
    entry = good_entry(max_targets="2").replace("max_items: 2", "max_items: 1")
    rule = load_tools(narrow_file(tmp_path, entry)).propose["batch_recording"]
    assert rule.schema.fields["device_ids"].max_items == 1
    with pytest.raises(ValueError, match="schema"):
        load_tools(narrow_file(tmp_path, good_entry().replace("          max_items: 2\n", "")))


def test_narrowing_file_cannot_change_a_schema(tmp_path, packaged_with_a_reviewed_tool):
    entry = good_entry().replace("values: [start, stop]", "values: [start, stop, wipe]")
    with pytest.raises(ValueError, match="schema"):
        load_tools(narrow_file(tmp_path, entry))


def test_narrowing_file_against_the_shipped_package_cannot_add_proposability(tmp_path):
    tool = next(iter(load_tools().propose))
    with pytest.raises(ValueError, match="schema"):
        entry = good_entry(tool=tool, max_targets="1", disruptive="true").replace("max_items: 2", "max_items: 1")
        load_tools(narrow_file(tmp_path, entry))


# --- autonomy: propose -----------------------------------------------------------------------------------------------
def test_autonomy_defaults_to_observe(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("heartbeat_seconds: 180\n")
    pol = load_policy(p)
    assert pol.autonomy == "observe" and not pol.proposes


def test_autonomy_propose_is_accepted(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("autonomy: propose\ndry_run: false\n")
    pol = load_policy(p)
    assert pol.autonomy == "propose" and pol.proposes
    assert pol.dry_run is True, "nothing runs a write, whatever the file says"


@pytest.mark.parametrize("value", ["auto", "act", "write", "PROPOSE", "", "true", "[propose]"])
def test_any_other_autonomy_fails(tmp_path, value):
    p = tmp_path / "policy.yaml"
    p.write_text(f"autonomy: {value}\n")
    with pytest.raises(ValueError, match="autonomy"):
        load_policy(p)


@pytest.mark.parametrize("tool", sorted(KNOWN_WRITE_TOOLS))
def test_guard_refuses_every_write_tool_even_in_propose_mode(tmp_path, tool):
    p = tmp_path / "policy.yaml"
    p.write_text("autonomy: propose\n")
    assert load_policy(p).proposes
    client = EpiphanClient("https://example.invalid/mcp", load_tools(), static_token="x")
    with pytest.raises(ToolNotAllowed):
        client.guard(tool)
