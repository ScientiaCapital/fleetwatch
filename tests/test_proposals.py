"""v0.2 proposal store: canonical arguments, the keyed binding, and single-use approvals that expire on the
database clock. Nothing here can run a write; it only stores and checks what a person approved."""

import sqlite3
import threading
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from fleetwatch.proposals import (
    BoundRecord,
    ProposalRefused,
    canonical,
    parse_canonical,
    sign,
    verify,
)
from fleetwatch.state import State

KEY = b"k" * 32
FP = {
    "dev-204": {"online": True, "recording": False, "next_event_start": "2026-10-07T16:00:00+00:00"},
    "dev-105": {"online": True, "recording": False, "next_event_start": None},
}


def _record(**changes) -> BoundRecord:
    base = BoundRecord(
        proposal_id=1,
        tool="batch_recording",
        arguments=canonical({"device_ids": ["dev-105", "dev-204"], "action": "start"}),
        targets=("dev-105", "dev-204"),
        fingerprint=FP,
        schema_version=1,
        slot="sandbox",
    )
    return replace(base, **changes)


def _propose(s: State, tool="batch_recording", targets=("dev-204", "dev-105"), **kw) -> int:
    fingerprint = {t: {"online": True, "recording": False, "next_event_start": None} for t in targets}
    return s.add_proposal(
        tool=tool,
        arguments={"device_ids": sorted(targets), "action": "start"},
        targets=list(targets),
        fingerprint=fingerprint,
        schema_version=1,
        slot="sandbox",
        reason="Room needs a recording",
        **kw,
    )


# --- canonical form ---------------------------------------------------------------------------------------
def test_canonical_is_sorted_compact_utf8():
    assert (
        canonical({"b": 1, "a": {"z": [3, 2], "y": "Salón 204"}}) == '{"a":{"y":"Salón 204","z":[3,2]},"b":1}'.encode()
    )


def test_canonical_is_stable_across_key_order_and_round_trip():
    a = canonical({"x": 1, "y": [True, None, "é"]})
    b = canonical({"y": [True, None, "é"], "x": 1})
    assert a == b
    assert canonical(parse_canonical(a)) == a
    assert canonical(a) == a  # bytes in, the same bytes out
    assert canonical(a.decode()) == a


def test_canonical_keeps_integers_as_integers():
    assert canonical({"n": 10**20, "z": 0}) == b'{"n":100000000000000000000,"z":0}'


@pytest.mark.parametrize("bad", [{"f": 1.0}, {"f": [0.5]}, {"f": float("nan")}, {"f": float("inf")}])
def test_canonical_refuses_floats_and_nan(bad):
    with pytest.raises(ValueError):
        canonical(bad)


@pytest.mark.parametrize(
    "text", ['{"a":1,"a":2}', '{"a":1.5}', '{"a":NaN}', '{"a":Infinity}', '{"a":-Infinity}', "[1]"]
)
def test_parsing_refuses_duplicates_floats_nan_and_non_objects(text):
    with pytest.raises(ValueError):
        canonical(text)


@pytest.mark.parametrize("bad", [{1: "x"}, {"a": object()}, {"a": (1, 2)}, ["not", "a", "dict"]])
def test_canonical_refuses_non_json_shapes(bad):
    with pytest.raises(ValueError):
        canonical(bad)


# --- binding ----------------------------------------------------------------------------------------------
def test_sign_and_verify_round_trip():
    r = _record()
    assert verify(r, sign(r, KEY), KEY)
    assert not verify(r, sign(r, KEY), b"x" * 32)


@pytest.mark.parametrize(
    "change",
    [
        {"proposal_id": 2},
        {"tool": "batch_reboot"},
        {"arguments": canonical({"device_ids": ["dev-105", "dev-204"], "action": "stop"})},
        {"targets": ("dev-105",)},
        {"fingerprint": {**FP, "dev-105": {**FP["dev-105"], "recording": True}}},
        {"fingerprint": {**FP, "dev-204": {**FP["dev-204"], "next_event_start": None}}},
        {"schema_version": 2},
        {"slot": "default"},
    ],
)
def test_hmac_changes_when_any_field_changes(change):
    assert sign(_record(), KEY) != sign(_record(**change), KEY)
    assert not verify(_record(**change), sign(_record(), KEY), KEY)


def test_default_secret_is_per_process_and_not_empty():
    import fleetwatch.proposals as p

    assert len(p._SECRET) == 32
    r = _record()
    assert verify(r, sign(r))


# --- proposals --------------------------------------------------------------------------------------------
def test_add_proposal_stores_sorted_targets_and_canonical_args():
    s = State()
    pid = _propose(s)
    rec = s.bound_record(pid)
    assert rec.targets == ("dev-105", "dev-204")
    assert rec.arguments == canonical({"action": "start", "device_ids": ["dev-105", "dev-204"]})
    assert rec.slot == "sandbox" and rec.schema_version == 1


def test_add_proposal_refuses_bad_fingerprint():
    s = State()
    with pytest.raises(ProposalRefused):
        s.add_proposal(
            tool="batch_recording",
            arguments={},
            targets=["dev-204"],
            fingerprint={"dev-999": {"online": True, "recording": False, "next_event_start": None}},
            schema_version=1,
            slot="sandbox",
        )


def test_add_proposal_refuses_no_targets():
    s = State()
    with pytest.raises(ProposalRefused):
        _propose(s, targets=())


def test_at_most_three_pending():
    s = State()
    for t in ("dev-1", "dev-2", "dev-3"):
        _propose(s, targets=(t,))
    with pytest.raises(ProposalRefused, match="three"):
        _propose(s, targets=("dev-4",))
    assert s.recent_audit("proposal_refused")[0][1]["why"]


def test_hourly_cap():
    s = State()
    for i in range(3):
        s.deny(_propose(s, targets=(f"dev-{i}",)))
    with pytest.raises(ProposalRefused, match="hour"):
        _propose(s, targets=("dev-9",), per_hour=3)
    # Proposals older than an hour no longer count (backdate on the database's own clock).
    s.db.execute("UPDATE proposals SET created_at = created_at - 3601")
    s.db.commit()
    _propose(s, targets=("dev-9",), per_hour=3)


def test_deny_circuit_breaker():
    s = State()
    for _ in range(3):
        assert s.deny(_propose(s))
    with pytest.raises(ProposalRefused, match="denied"):
        _propose(s)
    _propose(s, targets=("dev-301",))  # another target set is fine
    _propose(s, tool="batch_reboot")  # another tool is fine
    s.db.execute("UPDATE proposals SET decided_at = decided_at - 3601 WHERE status='denied'")
    s.db.commit()
    _propose(s)  # an hour after the third denial it may propose again


def test_deny_only_pending():
    s = State()
    pid = _propose(s)
    assert s.deny(pid)
    assert not s.deny(pid)
    with pytest.raises(ProposalRefused):
        s.approve(pid, "session-a")


# --- approvals --------------------------------------------------------------------------------------------
def test_approve_then_consume_returns_the_bound_record_once():
    s = State()
    pid = _propose(s)
    aid = s.approve(pid, "session-a")
    rec = s.consume(aid)
    assert rec is not None and rec.proposal_id == pid and rec.tool == "batch_recording"
    assert s.consume(aid) is None


def test_approve_twice_is_refused():
    s = State()
    pid = _propose(s)
    s.approve(pid, "session-a")
    with pytest.raises(ProposalRefused):
        s.approve(pid, "session-a")


def test_approval_expires_on_database_clock():
    s = State()
    aid = s.approve(_propose(s), "session-a")
    row = s.db.execute(
        "SELECT expires_at - CAST(strftime('%s','now') AS INTEGER) AS left FROM approvals WHERE id=?", (aid,)
    ).fetchone()
    assert 295 <= row["left"] <= 300
    s.db.execute("UPDATE approvals SET expires_at = CAST(strftime('%s','now') AS INTEGER) - 1 WHERE id=?", (aid,))
    s.db.commit()
    assert s.consume(aid) is None
    assert s.recent_audit("consume_refused")


def test_consume_unknown_approval():
    assert State().consume(12345) is None


def test_tampered_row_fails_hmac():
    s = State()
    aid = s.approve(_propose(s), "session-a")
    s.db.execute("""UPDATE proposals SET arguments='{"action":"stop","device_ids":["dev-105","dev-204"]}'""")
    s.db.commit()
    assert s.consume(aid) is None
    assert s.recent_audit("consume_refused")[0][1]["why"] == "binding check failed"


def test_record_from_another_process_fails_hmac(monkeypatch):
    import fleetwatch.proposals as p

    s = State()
    aid = s.approve(_propose(s), "session-a")
    monkeypatch.setattr(p, "_SECRET", b"n" * 32)  # a restarted process has a new secret
    assert s.consume(aid) is None


def test_expire_all_pending_on_start():
    s = State()
    p1 = _propose(s, targets=("dev-1",))
    p2 = _propose(s, targets=("dev-2",))
    aid = s.approve(p2, "session-a")
    assert s.expire_all_pending() == 2
    assert s.consume(aid) is None
    with pytest.raises(ProposalRefused):
        s.approve(p1, "session-a")
    _propose(s, targets=("dev-3",))  # the pending slots are free again
    assert s.recent_audit("expiry")[0][1]["proposals"] == 2


def test_concurrent_consume_only_one_wins(tmp_path):
    path = tmp_path / "state.db"
    s = State(path)
    aid = s.approve(_propose(s), "session-a")
    results = []
    barrier = threading.Barrier(2)

    def go():
        st = State(path)  # its own connection, as a second process would have
        barrier.wait()
        results.append(st.consume(aid))

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(r is not None for r in results) == 1


# --- outcomes ---------------------------------------------------------------------------------------------
def test_outcome_is_recorded_once_and_is_final():
    s = State()
    aid = s.approve(_propose(s), "session-a")
    assert s.outcome(aid) is None  # not consumed yet
    s.consume(aid)
    assert s.outcome(aid) == "unknown"  # consumed, nothing recorded: treat as unknown
    assert s.record_outcome(aid, "unknown", "no reply")
    assert not s.record_outcome(aid, "ok")
    assert s.outcome(aid) == "unknown"


def test_outcome_needs_a_consumed_approval_and_a_known_value():
    s = State()
    aid = s.approve(_propose(s), "session-a")
    assert not s.record_outcome(aid, "ok")
    s.consume(aid)
    with pytest.raises(ValueError):
        s.record_outcome(aid, "retry")
    assert s.record_outcome(aid, "ok")


# --- audit ------------------------------------------------------------------------------------------------
def test_every_step_is_audited_and_redacted():
    s = State()
    pid = s.add_proposal(
        tool="create_stream_endpoint",
        arguments={"name": "Room 204", "url": "rtmp://live.example.com/app/sk_live_abc123", "stream_key": "abc123"},
        targets=["dev-204"],
        fingerprint={"dev-204": {"online": True, "recording": False, "next_event_start": None}},
        schema_version=1,
        slot="sandbox",
    )
    aid = s.approve(pid, "session-a")
    s.consume(aid)
    s.record_outcome(aid, "error", "rejected")
    s.deny(_propose(s))
    kinds = [k for _, k, _ in s.audit_since(datetime(2000, 1, 1, tzinfo=UTC))]
    for kind in ("proposal", "approval", "consumed", "outcome", "denial"):
        assert kind in kinds
    text = str(s.db.execute("SELECT group_concat(detail) FROM audit").fetchone()[0])
    assert "abc123" not in text and "session-a" not in text
    (_, approval), *_ = s.recent_audit("approval")
    assert approval["approvals_this_session"] == 1


def test_approvals_per_session_count():
    s = State()
    s.approve(_propose(s, targets=("dev-1",)), "session-a")
    s.approve(_propose(s, targets=("dev-2",)), "session-a")
    s.approve(_propose(s, targets=("dev-3",)), "session-b")
    counts = [d["approvals_this_session"] for _, d in s.recent_audit("approval")]
    assert counts == [1, 2, 1]


# --- migration --------------------------------------------------------------------------------------------
def test_old_database_gets_proposal_tables(tmp_path):
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
        INSERT INTO audit (at, kind, detail) VALUES ('2026-10-01T00:00:00+00:00', 'heartbeat', '{}');
        """
    )
    old.commit()
    old.close()

    s = State(path)
    aid = s.approve(_propose(s), "session-a")
    assert s.consume(aid) is not None
    assert s.recent_audit("heartbeat")  # old rows kept
    State(path)  # opening again changes nothing


def test_row_edited_into_bad_json_is_refused_not_raised():
    s = State()
    pid = _propose(s)
    s.db.execute("UPDATE proposals SET fingerprint='{\"dev-105\": 1.5,' WHERE id=?", (pid,))
    s.db.commit()
    with pytest.raises(ProposalRefused):
        s.approve(pid, "session-a")
    s.db.execute("UPDATE proposals SET status='approved' WHERE id=?", (pid,))
    s.db.execute(
        "INSERT INTO approvals (proposal_id, created_at, expires_at) VALUES"
        " (?, CAST(strftime('%s','now') AS INTEGER), CAST(strftime('%s','now') AS INTEGER) + 300)",
        (pid,),
    )
    s.db.commit()
    aid = s.db.execute("SELECT max(id) FROM approvals").fetchone()[0]
    assert s.consume(aid) is None
    assert s.outcome(aid) == "error"
