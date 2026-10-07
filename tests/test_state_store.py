"""What later features read back from the state DB: devices, readiness details, findings with impact and fix,
the audit log and snapshots. Also: a DB written by an older Fleetwatch upgrades in place."""

import sqlite3
from datetime import timedelta

from fleetwatch.model import Channel, Device, Event, Finding, Fleet, Priority, Readiness
from fleetwatch.state import State
from tests.conftest import NOW

REMIND = timedelta(hours=4)


def _fleet() -> Fleet:
    return Fleet(
        taken_at=NOW,
        devices={
            "d1": Device(id="d1", name="Main Stage", model="Pearl-2", group="Hall", firmware="4.24.6"),
            "d2": Device(id="d2", name="Ballroom B", model="Pearl Mini", online=False),
            "d3": Device(
                id="d3",
                name="Ballroom B Camera",
                model="EC20",
                channels={"1": Channel(id="1", name="P", recording=True)},
            ),
        },
    )


def test_devices_are_upserted_and_listed_by_name():
    s = State()
    s.record_devices(_fleet(), NOW)
    rows = s.devices()
    assert [d.name for d in rows] == ["Ballroom B", "Ballroom B Camera", "Main Stage"]
    stage = next(d for d in rows if d.id == "d1")
    assert (stage.model, stage.group, stage.online, stage.firmware) == ("Pearl-2", "Hall", True, "4.24.6")
    assert next(d for d in rows if d.id == "d3").recording

    renamed = _fleet()
    renamed.devices["d1"].name = "Stage One"
    renamed.devices["d2"].online = True
    s.record_devices(renamed, NOW + timedelta(minutes=3))
    rows = {d.id: d for d in s.devices()}
    assert rows["d1"].name == "Stage One" and rows["d2"].online
    assert rows["d1"].last_seen == NOW + timedelta(minutes=3)


def test_find_devices_matches_case_insensitive_substring_and_prefers_exact():
    s = State()
    s.record_devices(_fleet(), NOW)
    assert [d.id for d in s.find_devices("ballroom")] == ["d2", "d3"]
    assert [d.id for d in s.find_devices("ballroom b")] == ["d2"]  # exact name wins over the longer match
    assert [d.id for d in s.find_devices("STAGE")] == ["d1"]
    assert s.find_devices("nowhere") == []
    assert s.find_devices("%") == []  # LIKE wildcards are literal text, not patterns
    assert s.find_devices("  ") == []


def test_readiness_details_round_trip():
    s = State()
    event = Event(device_id="d1", title="Opening keynote", start=NOW + timedelta(minutes=30), id="e1")
    r = Readiness(event=event, device_name="Main Stage", verdict="Not ready", notes=("No picture on Camera 2",))
    assert not s.readiness_posted(event.key)
    s.record_readiness(r, NOW)
    assert s.readiness_posted(event.key)
    got = s.latest_readiness("d1")
    assert got is not None
    assert (got.title, got.verdict, got.notes, got.start) == (
        "Opening keynote",
        "Not ready",
        ("No picture on Camera 2",),
        event.start,
    )
    assert s.latest_readiness("nope") is None


def test_findings_keep_impact_and_fix():
    s = State()
    f = Finding(
        key="d2:offline",
        priority=Priority.FIX_FIRST,
        device_id="d2",
        device_name="Ballroom B",
        what="Ballroom B is offline",
        impact="Events in that room won't record",
        fix="Check power",
    )
    s.reconcile([f], NOW, REMIND)
    (back,) = s.open_findings()
    assert (back.impact, back.fix) == (f.impact, f.fix)


def test_audit_and_snapshots_read_back_newest_first():
    s = State()
    s.audit("digest", {"new": ["a"]}, NOW)
    s.audit("readiness", {"event": "e"}, NOW + timedelta(minutes=1))
    s.audit("digest", {"new": ["b"]}, NOW + timedelta(minutes=2))
    assert [d["new"] for _, d in s.recent_audit("digest", 5)] == [["b"], ["a"]]
    assert len(s.recent_audit("digest", 1)) == 1

    s.snapshot(NOW - timedelta(days=2), 10, 9)
    s.snapshot(NOW, 10, 8)
    assert [(n, on) for _, n, on in s.snapshots_since(NOW - timedelta(days=1))] == [(10, 8)]


def test_old_database_upgrades_in_place(tmp_path):
    path = tmp_path / "state.db"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE findings (
          key TEXT PRIMARY KEY, device_id TEXT, device_name TEXT, priority TEXT, what TEXT, fyi INTEGER,
          first_seen TEXT, last_seen TEXT, last_sent TEXT, resolved_at TEXT);
        CREATE TABLE readiness (event_key TEXT PRIMARY KEY, posted_at TEXT, verdict TEXT);
        CREATE TABLE audit (id INTEGER PRIMARY KEY, at TEXT, kind TEXT, detail TEXT);
        CREATE TABLE snapshots (id INTEGER PRIMARY KEY, at TEXT, devices INTEGER, online INTEGER);
        INSERT INTO findings VALUES ('x:offline','x','Room X','Fix first','Room X is offline',0,
          '2026-10-01T00:00:00+00:00','2026-10-01T00:00:00+00:00',NULL,NULL);
        INSERT INTO readiness VALUES ('x:e0','2026-10-01T00:00:00+00:00','Ready');
        """
    )
    old.commit()
    old.close()

    s = State(path)
    (f,) = s.open_findings()
    assert (f.key, f.impact, f.fix) == ("x:offline", "", "")
    assert s.readiness_posted("x:e0")
    s.record_devices(_fleet(), NOW)
    assert len(s.devices()) == 3
    State(path)  # opening twice must not try to add the columns again
