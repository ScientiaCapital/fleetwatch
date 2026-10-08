"""The write fence (docs/design/approved-writes.md, "Fence"): a team ID and/or an allowlist of sandbox device IDs."""

from fleetwatch.config import Settings
from fleetwatch.fence import Fence


def _s(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_no_team_and_no_allowlist_is_not_a_fence():
    fence = Fence.from_settings(_s())
    assert not fence.is_set
    assert fence.allows("0a1b2c3d") is False, "an unset fence allows nothing"


def test_a_team_id_alone_is_a_fence_and_allows_any_device_the_team_reaches():
    fence = Fence.from_settings(_s(write_team_id=" team-sandbox "))
    assert fence.is_set and fence.team_id == "team-sandbox"
    assert fence.allows("0a1b2c3d") is True


def test_an_allowlist_allows_only_its_devices_ignoring_case_spaces_and_blanks():
    fence = Fence.from_settings(_s(write_device_ids=" 0A1B2C3D , ,0e0f1a2b,"))
    assert fence.is_set
    assert fence.allows("0a1b2c3d") and fence.allows("0E0F1A2B")
    assert fence.allows("0fffffff") is False


def test_a_channel_id_is_checked_by_its_master_device():
    fence = Fence.from_settings(_s(write_device_ids="0a1b2c3d"))
    assert fence.allows("0a1b2c3d-2") is True
    assert fence.allows("0fffffff-2") is False
