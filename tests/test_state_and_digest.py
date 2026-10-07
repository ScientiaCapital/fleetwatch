from datetime import timedelta

from fleetwatch.model import Finding, Priority
from fleetwatch.notify.digest import render_digest
from fleetwatch.state import State
from tests.conftest import NOW

A = Finding(
    key="a:offline",
    priority=Priority.FIX_FIRST,
    device_id="a",
    device_name="Room A",
    what="Room A is offline",
    impact="No class records",
    fix="Check power",
)
B = Finding(
    key="b:firmware",
    priority=Priority.FIX_SOON,
    device_id="b",
    device_name="Room B",
    what="Room B is behind on firmware",
)
FYI = Finding(
    key="fleet:storage",
    priority=Priority.WHEN_CONVENIENT,
    device_id="",
    device_name="",
    what="FYI: 2 Pearls have little space",
    fyi=True,
)
REMIND = timedelta(hours=4)


def test_same_problem_posts_once_then_reminds_then_resolves():
    s = State()
    new, rem, res = s.reconcile([A, FYI], NOW, REMIND)
    assert [f.key for f in new] == ["a:offline", "fleet:storage"] and not rem and not res
    s.mark_sent(new, NOW)
    new, rem, res = s.reconcile([A, FYI], NOW + timedelta(minutes=3), REMIND)
    assert not new and not rem and not res, "nothing re-posted three minutes later"
    new, rem, res = s.reconcile([A, FYI], NOW + timedelta(hours=5), REMIND)
    assert [f.key for f in rem] == ["a:offline"], "FYIs never remind"
    new, rem, res = s.reconcile([FYI], NOW + timedelta(hours=6), REMIND)
    assert [f.key for f in res] == ["a:offline"] and all(f.key != "a:offline" for f in s.open_findings())


def test_unsent_item_is_still_new_next_beat():
    s = State()
    s.reconcile([B], NOW, REMIND)  # quiet hours: not sent
    new, _, _ = s.reconcile([B], NOW + timedelta(hours=1), REMIND)
    assert [f.key for f in new] == ["b:firmware"]


def test_resolved_only_if_it_was_ever_posted():
    s = State()
    s.reconcile([B], NOW, REMIND)
    _, _, res = s.reconcile([], NOW + timedelta(hours=1), REMIND)
    assert res == []


def test_digest_tone_and_shape():
    text = render_digest([B, A, FYI], [], [], first_run=True)
    assert text.index("Fix first") < text.index("Fix soon"), "Fix first comes first"
    assert text.rstrip().endswith("That's normal when recordings upload to your CMS.") or "FYI:" in text
    assert "P1" not in text and "critical" not in text.lower() and "—" not in text
    assert render_digest([], [], []) is None, "nothing to say means no post"
    assert render_digest([FYI], [], []) is None, "an FYI alone is not worth a post"
    assert "All clear" in render_digest([], [], [], first_run=True)
    assert "Back to normal" in render_digest([], [], [A])


def test_readiness_posted_once():
    s = State()
    assert not s.readiness_posted("d:e1")
    s.mark_readiness("d:e1", "Ready", NOW)
    assert s.readiness_posted("d:e1")
