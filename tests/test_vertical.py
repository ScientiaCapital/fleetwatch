"""One setting picks the words: a venue has events, a campus has classes, a court has hearings."""

from datetime import UTC, datetime

import pytest

from fleetwatch.agents.readiness.rules import check
from fleetwatch.agents.scanner.rules import scan
from fleetwatch.model import Device, Event, Fleet
from fleetwatch.policy import VERTICALS, Policy, load_policy

NOW = datetime(2026, 10, 7, 15, 0, tzinfo=UTC)


def _offline_fleet() -> Fleet:
    return Fleet(taken_at=NOW, devices={"d1": Device(id="d1", name="Main Stage", model="Pearl-2", online=False)})


def _impacts(policy: Policy) -> str:
    return " ".join(f.impact for f in scan(_offline_fleet(), policy))


def test_default_vertical_is_live_events():
    assert Policy().vertical == "events"
    assert "Events in that room" in _impacts(Policy())


@pytest.mark.parametrize(
    ("vertical", "plural"),
    [("education", "Classes"), ("business", "Meetings"), ("courts", "Hearings"), ("worship", "Services")],
)
def test_each_vertical_uses_its_own_word(vertical, plural):
    text = _impacts(Policy(vertical=vertical))
    assert f"{plural} in that room" in text
    assert "class" not in text.lower() or vertical == "education"


def test_readiness_uses_the_vertical_word():
    event = Event(device_id="d1", title="Keynote", start=NOW, id="e1")
    device = _offline_fleet().devices["d1"]
    assert "the event won't record" in " ".join(check(device, event, Policy()).notes)
    assert "the hearing won't record" in " ".join(check(device, event, Policy(vertical="courts")).notes)


def test_readiness_for_an_offline_camera_doesnt_say_it_records():
    """Camera or encoder comes from the model Edge reports. The name here says Pearl on purpose."""
    event = Event(device_id="c1", title="Keynote", start=NOW, id="e1")
    cam = Device(id="c1", name="Main Stage Pearl-2", model="EC20", online=False)
    r = check(cam, event, Policy())
    assert r.verdict == "Not ready"
    assert r.notes == (
        (
            "The camera is offline, so its picture may be missing from the Pearl channels that use it, "
            "and Edge can't control it"
        ),
    )


def test_every_vertical_has_singular_and_plural():
    for words in VERTICALS.values():
        assert len(words) == 2 and all(words)


def test_unknown_vertical_is_refused(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("vertical: circus\n")
    with pytest.raises(ValueError, match="vertical"):
        load_policy(p)


def test_lead_minutes_and_the_old_name_both_load(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("lead_minutes: 45\n")
    assert load_policy(p).lead_minutes == 45
    p.write_text("preclass_lead_minutes: 20\n")
    assert load_policy(p).lead_minutes == 20
