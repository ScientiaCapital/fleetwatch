"""What the agent remembers between heartbeats: open findings, what was posted when, and an audit log.
SQLite, one file. Without this every heartbeat would re-post the same items."""

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fleetwatch.model import Finding, Fleet, Readiness, SweepResult
from fleetwatch.proposals import (
    BoundRecord,
    ProposalRefused,
    canonical,
    normalize_fingerprint,
    parse_canonical,
    sign,
    verify,
)
from fleetwatch.redact import encodable, redact

_SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
  key TEXT PRIMARY KEY, device_id TEXT, device_name TEXT, priority TEXT, what TEXT, fyi INTEGER,
  first_seen TEXT, last_seen TEXT, last_sent TEXT, resolved_at TEXT);
CREATE TABLE IF NOT EXISTS readiness (event_key TEXT PRIMARY KEY, posted_at TEXT, verdict TEXT);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at TEXT, kind TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY, at TEXT, devices INTEGER, online INTEGER);
CREATE TABLE IF NOT EXISTS sweeps (
  id INTEGER PRIMARY KEY, at TEXT, devices INTEGER, online INTEGER, offline TEXT, behind TEXT, newly_offline TEXT,
  back_online TEXT, posted_at TEXT);
CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY, name TEXT, model TEXT, group_name TEXT, online INTEGER, firmware TEXT, recording INTEGER,
  last_seen TEXT);
CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, device_id TEXT, note TEXT, author TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY, created_at INTEGER NOT NULL, tool TEXT NOT NULL, arguments TEXT NOT NULL,
  targets TEXT NOT NULL, fingerprint TEXT NOT NULL, schema_version INTEGER NOT NULL, slot TEXT NOT NULL,
  reason TEXT, mac TEXT, status TEXT NOT NULL DEFAULT 'pending', decided_at INTEGER);
CREATE INDEX IF NOT EXISTS proposals_status ON proposals (status);
CREATE INDEX IF NOT EXISTS proposals_pair ON proposals (tool, targets, status);
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY, proposal_id INTEGER NOT NULL REFERENCES proposals (id), session TEXT,
  created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0, consumed_at INTEGER,
  outcome TEXT, outcome_detail TEXT, outcome_at INTEGER);
"""

# Proposal times are whole seconds on SQLite's own clock, never Python's, so the process can't move expiry.
_DB_NOW = "CAST(strftime('%s','now') AS INTEGER)"
APPROVAL_SECONDS = 5 * 60
MAX_PENDING = 3
PROPOSALS_PER_HOUR = 10
DENIALS_TO_PAUSE = 3
PAUSE_SECONDS = 60 * 60
OUTCOMES = ("ok", "error", "unknown")
_TEXT_CAP = 500

# Columns added after v0.1's first schema. Old databases get them on open; new ones go through the same path.
_ADDED_COLUMNS = {
    "findings": ("impact TEXT", "fix TEXT"),
    "readiness": ("device_id TEXT", "device_name TEXT", "title TEXT", "start TEXT", "notes TEXT"),
    "audit": ("prev_hash TEXT", "hash TEXT"),  # the chain starts at the first row written with a hash
    "approvals": ("started_at INTEGER",),  # set once, when the write executor takes a consumed approval
}


@dataclass(frozen=True)
class KnownDevice:
    """A device as last seen by a heartbeat. The name is untrusted text: match it, never act on it."""

    id: str
    name: str
    model: str
    group: str
    online: bool
    firmware: str
    recording: bool
    last_seen: datetime


@dataclass(frozen=True)
class PostedReadiness:
    device_id: str
    device_name: str
    title: str
    start: datetime | None
    verdict: str
    notes: tuple[str, ...]
    posted_at: datetime


@dataclass(frozen=True)
class Note:
    """A note a person left on a room. Cleaned before it is stored (see fleetwatch.notes), still untrusted."""

    id: int
    device_id: str
    note: str
    author: str
    at: datetime


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat() if dt else None


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


class State:
    def __init__(self, path: Path | str = ":memory:", *, check_same_thread: bool = True):
        """`check_same_thread=False` is for the approval page, whose one-request-at-a-time server may answer on
        another thread than the one that opened the file. Calls still never overlap."""
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=check_same_thread)
        self.db.row_factory = sqlite3.Row
        # `ask --serve`, `status` and the heartbeat may open the same file at once: wait for a lock, don't fail.
        self.db.execute("PRAGMA busy_timeout=5000")
        if path != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")  # readers don't block the heartbeat's writes
        self.db.executescript(_SCHEMA)
        self._upgrade()

    def _upgrade(self) -> None:
        for table, columns in _ADDED_COLUMNS.items():
            have = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
            for column in columns:
                if column.split()[0] not in have:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
        self.db.commit()

    # --- findings -----------------------------------------------------------------------------------
    def reconcile(
        self, findings: list[Finding], now: datetime, remind_after: timedelta
    ) -> tuple[list[Finding], list[Finding], list[Finding]]:
        """Compare this heartbeat's findings with what's open. Returns (new, reminders, resolved).
        Updates last_seen, marks vanished items resolved. Does NOT mark anything as sent: call mark_sent."""
        open_rows = {r["key"]: r for r in self.db.execute("SELECT * FROM findings WHERE resolved_at IS NULL")}
        new, reminders, resolved = [], [], []
        for f in findings:
            row = open_rows.pop(f.key, None)
            if row is None:
                self.db.execute(
                    "INSERT OR REPLACE INTO findings (key, device_id, device_name, priority, what, fyi, first_seen,"
                    " last_seen, last_sent, resolved_at, impact, fix) VALUES (?,?,?,?,?,?,?,?,NULL,NULL,?,?)",
                    (
                        f.key,
                        f.device_id,
                        f.device_name,
                        f.priority.value,
                        f.what,
                        int(f.fyi),
                        _iso(now),
                        _iso(now),
                        f.impact,
                        f.fix,
                    ),
                )
                new.append(f)
            else:
                self.db.execute(
                    "UPDATE findings SET last_seen=?, impact=?, fix=? WHERE key=?", (_iso(now), f.impact, f.fix, f.key)
                )
                last_sent = _dt(row["last_sent"])
                if last_sent is None:
                    new.append(f)  # seen before but never posted (quiet hours)
                elif not f.fyi and now - last_sent >= remind_after:
                    reminders.append(f)
        for key, row in open_rows.items():
            self.db.execute("UPDATE findings SET resolved_at=? WHERE key=?", (_iso(now), key))
            if row["last_sent"] is not None and not row["fyi"]:
                resolved.append(_finding_from_row(row))
        self.db.commit()
        return new, reminders, resolved

    def mark_sent(self, findings: list[Finding], now: datetime) -> None:
        self.db.executemany("UPDATE findings SET last_sent=? WHERE key=?", [(_iso(now), f.key) for f in findings])
        self.db.commit()

    def open_findings(self) -> list[Finding]:
        return [
            _finding_from_row(r)
            for r in self.db.execute("SELECT * FROM findings WHERE resolved_at IS NULL ORDER BY first_seen")
        ]

    # --- readiness ----------------------------------------------------------------------------------
    def readiness_posted(self, event_key: str) -> bool:
        return self.db.execute("SELECT 1 FROM readiness WHERE event_key=?", (event_key,)).fetchone() is not None

    def readiness_verdict(self, event_key: str) -> str | None:
        """The verdict last posted for this event occurrence, or None if nothing was posted yet.
        `record_readiness` overwrites the row, so this is always the newest one."""
        row = self.db.execute("SELECT verdict FROM readiness WHERE event_key=?", (event_key,)).fetchone()
        return None if row is None else row["verdict"]

    def record_readiness(self, r: Readiness, now: datetime) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO readiness (event_key, posted_at, verdict, device_id, device_name, title, start,"
            " notes) VALUES (?,?,?,?,?,?,?,?)",
            (
                r.event.key,
                _iso(now),
                r.verdict,
                r.event.device_id,
                r.device_name,
                r.event.title,
                _iso(r.event.start),
                json.dumps(list(r.notes)),
            ),
        )
        self.db.commit()

    def latest_readiness(self, device_id: str) -> PostedReadiness | None:
        row = self.db.execute(
            "SELECT * FROM readiness WHERE device_id=? ORDER BY posted_at DESC LIMIT 1", (device_id,)
        ).fetchone()
        if row is None:
            return None
        return PostedReadiness(
            device_id=row["device_id"],
            device_name=row["device_name"] or "",
            title=row["title"] or "",
            start=_dt(row["start"]),
            verdict=row["verdict"],
            notes=tuple(json.loads(row["notes"] or "[]")),
            posted_at=_dt(row["posted_at"]),
        )

    # --- devices ------------------------------------------------------------------------------------
    def record_devices(self, fleet: Fleet, now: datetime) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO devices (id, name, model, group_name, online, firmware, recording, last_seen)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [
                (d.id, d.name, d.model, d.group, int(d.online), d.firmware, int(d.recording), _iso(now))
                for d in fleet.devices.values()
            ],
        )
        self.db.commit()

    def devices(self) -> list[KnownDevice]:
        rows = self.db.execute("SELECT * FROM devices").fetchall()
        return sorted((_device_from_row(r) for r in rows), key=lambda d: (d.name.casefold(), d.id))

    def find_devices(self, text: str) -> list[KnownDevice]:
        """Devices whose name contains `text`, ignoring case. An exact name match wins on its own."""
        needle = " ".join(text.split()).casefold()
        if not needle:
            return []
        hits = [d for d in self.devices() if needle in " ".join(d.name.split()).casefold()]
        exact = [d for d in hits if " ".join(d.name.split()).casefold() == needle]
        return exact or hits

    # --- notes --------------------------------------------------------------------------------------
    def add_note(self, device_id: str, note: str, author: str, now: datetime) -> int:
        """Store a note as given. Callers clean it first: fleetwatch.notes.add_note does."""
        cur = self.db.execute(
            "INSERT INTO notes (device_id, note, author, at) VALUES (?,?,?,?)", (device_id, note, author, _iso(now))
        )
        self.db.commit()
        return int(cur.lastrowid)

    def notes(self, device_id: str | None = None) -> list[Note]:
        """Newest first; all rooms, or one."""
        if device_id is None:
            rows = self.db.execute("SELECT * FROM notes ORDER BY at DESC, id DESC")
        else:
            rows = self.db.execute("SELECT * FROM notes WHERE device_id=? ORDER BY at DESC, id DESC", (device_id,))
        return [Note(r["id"], r["device_id"], r["note"] or "", r["author"] or "", _dt(r["at"])) for r in rows]

    def notes_by_device(self, device_ids: set[str], limit: int = 3) -> dict[str, list[Note]]:
        """The newest `limit` notes for each of these devices; devices without notes are left out."""
        out: dict[str, list[Note]] = {}
        for n in self.notes():
            if n.device_id in device_ids and len(out.setdefault(n.device_id, [])) < limit:
                out[n.device_id].append(n)
        return {k: v for k, v in out.items() if v}

    # --- proposals and approvals (v0.2: storage and binding only; nothing here runs a write) ---------
    @contextmanager
    def _immediate(self) -> Iterator[None]:
        """One write transaction that takes the lock up front, so a check and the write after it can't
        interleave with another process doing the same."""
        if self.db.in_transaction:
            self.db.commit()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.rollback()
            raise
        self.db.commit()

    def _db_now(self) -> int:
        return int(self.db.execute(f"SELECT {_DB_NOW}").fetchone()[0])

    def add_proposal(
        self,
        tool: str,
        arguments: dict[str, Any],
        targets: list[str],
        fingerprint: dict[str, Any],
        schema_version: int,
        slot: str,
        reason: str = "",
        per_hour: int = PROPOSALS_PER_HOUR,
    ) -> int:
        """Store a pending proposal and return its ID. Raises ProposalRefused with a plain reason when the
        arguments can't be bound or a limit says no: three already pending, `per_hour` made in the last hour, or
        the same tool and targets denied three times within an hour (paused for an hour after the third denial).
        Callers have already checked the tool against the propose policy and resolved targets to device IDs."""
        unique = tuple(sorted(set(targets)))
        stored_reason = _clean_text(reason)
        target_key = json.dumps(list(unique))
        try:
            if not isinstance(tool, str) or not tool:
                raise ProposalRefused("No tool was named.")
            if not unique or not all(isinstance(t, str) and t for t in unique):
                raise ProposalRefused("A change needs at least one target device ID.")
            if not isinstance(slot, str) or not slot:
                raise ProposalRefused("No sign-in slot was named.")
            if not isinstance(schema_version, int) or isinstance(schema_version, bool):
                raise ProposalRefused("The schema version must be a whole number.")
            try:
                args = canonical(arguments)
                fp = normalize_fingerprint(unique, fingerprint)
            except ValueError as e:  # NotCanonical, or bad UTF-8 / JSON text
                raise ProposalRefused(f"The arguments can't be bound: {e}") from None
            with self._immediate():
                now = self._db_now()
                pending = self.db.execute("SELECT COUNT(*) FROM proposals WHERE status='pending'").fetchone()[0]
                if pending >= MAX_PENDING:
                    raise ProposalRefused("There are already three changes waiting. Approve or deny one first.")
                # A person's denial isn't the model's budget; expired and unreviewed proposals still count.
                recent = self.db.execute(
                    "SELECT COUNT(*) FROM proposals WHERE created_at > ? AND status != 'denied'", (now - 3600,)
                ).fetchone()[0]
                if recent >= per_hour:
                    raise ProposalRefused(f"That's {recent} proposals in the last hour, the most allowed.")
                denials = [
                    r[0]
                    for r in self.db.execute(
                        "SELECT decided_at FROM proposals WHERE status='denied' AND tool=? AND targets=?"
                        " ORDER BY decided_at DESC LIMIT ?",
                        (tool, target_key, DENIALS_TO_PAUSE),
                    )
                ]
                if (
                    len(denials) == DENIALS_TO_PAUSE
                    and denials[0] - denials[-1] <= PAUSE_SECONDS
                    and now - denials[0] < PAUSE_SECONDS
                ):
                    raise ProposalRefused("This change was denied three times. It can't be proposed again for an hour.")
                cur = self.db.execute(
                    "INSERT INTO proposals (created_at, tool, arguments, targets, fingerprint, schema_version, slot,"
                    " reason) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        now,
                        tool,
                        args.decode("utf-8"),
                        target_key,
                        canonical(fp).decode("utf-8"),
                        schema_version,
                        slot,
                        stored_reason,
                    ),
                )
                pid = int(cur.lastrowid)
                record = BoundRecord(pid, tool, args, unique, fp, schema_version, slot, stored_reason)
                self.db.execute("UPDATE proposals SET mac=? WHERE id=?", (sign(record), pid))
        except ProposalRefused as e:
            self.audit("proposal_refused", {"tool": str(tool), "targets": list(unique), "why": e.reason})
            raise
        self.audit("proposal", {**_audit_record(record), "reason": stored_reason})
        return pid

    def _record_from_row(self, row: sqlite3.Row) -> BoundRecord:
        return BoundRecord(
            proposal_id=int(row["id"]),
            tool=row["tool"],
            arguments=row["arguments"].encode("utf-8"),
            targets=tuple(json.loads(row["targets"])),
            fingerprint=parse_canonical(row["fingerprint"]),
            schema_version=int(row["schema_version"]),
            slot=row["slot"],
            reason=row["reason"] or "",
        )

    def _checked_record(self, row: sqlite3.Row) -> BoundRecord | None:
        """The row's bound record if it still parses and its keyed hash matches; None if it was edited or was
        signed by another process."""
        try:
            record = self._record_from_row(row)
        except (ValueError, TypeError, KeyError):
            return None
        return record if verify(record, row["mac"]) else None

    def bound_record(self, proposal_id: int) -> BoundRecord | None:
        """The stored record for a proposal, whatever its status, for showing a card. An executor uses
        consume(), never this."""
        row = self.db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        return None if row is None else self._record_from_row(row)

    def pending_proposals(self) -> list[tuple[int, BoundRecord | None, str]]:
        """Every pending proposal, oldest first, as (ID, record, the model's reason). The record is None when the
        row no longer parses or its keyed hash doesn't match; the approval page then offers only Deny."""
        rows = self.db.execute("SELECT * FROM proposals WHERE status='pending' ORDER BY created_at, id").fetchall()
        return [(int(r["id"]), self._checked_record(r), r["reason"] or "") for r in rows]

    def approve(self, proposal_id: int, page_session: str) -> int:
        """A person approved this pending proposal. Returns a single-use approval ID that expires five minutes
        from now on the database clock. Raises ProposalRefused if the proposal isn't pending or fails its check."""
        session = _session_tag(page_session)
        try:
            with self._immediate():
                row = self.db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
                if row is None or row["status"] != "pending":
                    raise ProposalRefused("This change isn't waiting for approval.")
                if self._checked_record(row) is None:
                    raise ProposalRefused("binding check failed")
                self.db.execute(
                    f"UPDATE proposals SET status='approved', decided_at={_DB_NOW} WHERE id=? AND status='pending'",
                    (proposal_id,),
                )
                cur = self.db.execute(
                    "INSERT INTO approvals (proposal_id, session, created_at, expires_at, used)"
                    f" VALUES (?, ?, {_DB_NOW}, {_DB_NOW} + ?, 0)",
                    (proposal_id, session, APPROVAL_SECONDS),
                )
                aid = int(cur.lastrowid)
                count = self.db.execute("SELECT COUNT(*) FROM approvals WHERE session=?", (session,)).fetchone()[0]
        except ProposalRefused as e:
            self.audit("approval_refused", {"proposal_id": proposal_id, "session": session, "why": e.reason})
            raise
        self.audit(
            "approval",
            {"proposal_id": proposal_id, "approval_id": aid, "session": session, "approvals_this_session": count},
        )
        return aid

    def queue_status(self) -> tuple[int, int, int, int]:
        """(pending now, counted in the last hour, the pending limit, the hourly limit): the same numbers and
        constants add_proposal enforces, so a page or `doctor` can say why a proposal was refused."""
        now = self._db_now()
        pending = self.db.execute("SELECT COUNT(*) FROM proposals WHERE status='pending'").fetchone()[0]
        hour = self.db.execute(
            "SELECT COUNT(*) FROM proposals WHERE created_at > ? AND status != 'denied'", (now - 3600,)
        ).fetchone()[0]
        return int(pending), int(hour), MAX_PENDING, PROPOSALS_PER_HOUR

    def deny_all(self, page_session: str | None = None) -> int:
        """A person denied everything waiting. Each proposal is denied by its own pending-only UPDATE, so it is
        counted and audited like a single denial. There is no approve-all: approval stays one proposal at a time."""
        ids = [int(r[0]) for r in self.db.execute("SELECT id FROM proposals WHERE status='pending' ORDER BY id")]
        return sum(1 for pid in ids if self.deny(pid, page_session))

    def deny(self, proposal_id: int, page_session: str | None = None) -> bool:
        """A person denied this pending proposal. False if it wasn't pending."""
        changed = self.db.execute(
            f"UPDATE proposals SET status='denied', decided_at={_DB_NOW} WHERE id=? AND status='pending'",
            (proposal_id,),
        ).rowcount
        self.db.commit()
        if changed == 1:
            row = self.db.execute("SELECT tool, targets FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            self.audit(
                "denial",
                {
                    "proposal_id": proposal_id,
                    "tool": row["tool"],
                    "targets": json.loads(row["targets"]),
                    "session": _session_tag(page_session) if page_session else None,
                },
            )
        return changed == 1

    def consume(self, approval_id: int) -> BoundRecord | None:
        """Use an approval, once. One UPDATE marks it used only if it is unused, unexpired on the database clock,
        and its proposal is approved; in any other case no row changes and this returns None. Then the keyed
        hash is checked again, so a row edited on disk, or one signed by an earlier process, is refused."""
        changed = self.db.execute(
            f"UPDATE approvals SET used=1, consumed_at={_DB_NOW} WHERE id=? AND used=0 AND expires_at > {_DB_NOW}"
            " AND proposal_id IN (SELECT id FROM proposals WHERE status='approved')",
            (approval_id,),
        ).rowcount
        self.db.commit()
        if changed != 1:
            self.audit("consume_refused", {"approval_id": approval_id, "why": "already used, expired or unknown"})
            return None
        row = self.db.execute(
            "SELECT p.* FROM proposals p JOIN approvals a ON a.proposal_id = p.id WHERE a.id=?", (approval_id,)
        ).fetchone()
        record = self._checked_record(row)
        if record is None:
            self.db.execute("UPDATE proposals SET status='refused' WHERE id=?", (row["id"],))
            self.db.execute(
                f"UPDATE approvals SET outcome='error', outcome_detail=?, outcome_at={_DB_NOW} WHERE id=?",
                ("binding check failed; nothing ran", approval_id),
            )
            self.db.commit()
            self.audit(
                "consume_refused",
                {"approval_id": approval_id, "proposal_id": row["id"], "why": "binding check failed"},
            )
            return None
        self.db.execute("UPDATE proposals SET status='consumed' WHERE id=?", (record.proposal_id,))
        self.db.commit()
        self.audit("consumed", {"approval_id": approval_id, **_audit_record(record)})
        return record

    def claim(self, record: BoundRecord) -> int | None:
        """The write executor takes a record that consume() returned, once. Returns its approval ID, or None when
        the record isn't exactly what was stored and signed (its keyed hash doesn't match), its approval wasn't
        consumed, or it was already claimed or has an outcome. One UPDATE marks it started, so two callers holding
        the same record can't both run it."""
        with self._immediate():
            row = self.db.execute(
                "SELECT p.*, a.id AS approval_id FROM proposals p JOIN approvals a ON a.proposal_id = p.id"
                " WHERE p.id=? AND p.status='consumed' AND a.consumed_at IS NOT NULL",
                (record.proposal_id,),
            ).fetchone()
            if row is None or not _signed(record, row["mac"]) or self._checked_record(row) != record:
                return None
            changed = self.db.execute(
                f"UPDATE approvals SET started_at={_DB_NOW}"
                " WHERE id=? AND consumed_at IS NOT NULL AND outcome IS NULL AND started_at IS NULL",
                (row["approval_id"],),
            ).rowcount
        return int(row["approval_id"]) if changed == 1 else None

    def record_outcome(self, approval_id: int, outcome: str, detail: str = "") -> bool:
        """What happened after a consumed approval: ok, error or unknown. Recorded once, and final. `unknown`
        means the write may or may not have happened; it is never retried. False if an outcome is already
        recorded or the approval was never consumed."""
        if outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}")
        changed = self.db.execute(
            f"UPDATE approvals SET outcome=?, outcome_detail=?, outcome_at={_DB_NOW}"
            " WHERE id=? AND consumed_at IS NOT NULL AND outcome IS NULL",
            (outcome, _clean_text(detail), approval_id),
        ).rowcount
        self.db.commit()
        if changed == 1:
            self.audit("outcome", {"approval_id": approval_id, "outcome": outcome, "detail": _clean_text(detail)})
        return changed == 1

    def outcome(self, approval_id: int) -> str | None:
        """None until the approval is consumed. Consumed with nothing recorded reads as `unknown`."""
        row = self.db.execute("SELECT consumed_at, outcome FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if row is None or row["consumed_at"] is None:
            return None
        return row["outcome"] or "unknown"

    def expire_all_pending(self) -> int:
        """Expire every pending or approved-but-unused proposal and every unused approval. The process that
        shows the approval page calls this when it starts. It isn't called on open: the heartbeat and `status`
        open the same file and must not expire another process's cards. Returns how many proposals expired."""
        with self._immediate():
            proposals = self.db.execute(
                f"UPDATE proposals SET status='expired', decided_at={_DB_NOW} WHERE status IN ('pending','approved')"
            ).rowcount
            approvals = self.db.execute("UPDATE approvals SET used=1 WHERE used=0").rowcount
        self.audit("expiry", {"proposals": proposals, "approvals": approvals})
        return proposals

    def expire_proposals(self, proposal_ids: list[int]) -> int:
        """Expire these proposals if they're still pending: the assistant's turn that made them failed partway,
        so no card may show them. Returns how many expired."""
        ids = [int(i) for i in proposal_ids]
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        with self._immediate():
            n = self.db.execute(
                f"UPDATE proposals SET status='expired', decided_at={_DB_NOW} WHERE status='pending' AND id IN ({marks})",
                ids,
            ).rowcount
        self.audit("expiry", {"proposal_ids": ids, "proposals": n, "why": "assistant turn failed"})
        return n

    # --- audit --------------------------------------------------------------------------------------
    def audit(self, kind: str, detail: dict, now: datetime | None = None) -> None:
        """Append one row. Each row stores a hash over its own content and the previous row's hash, so an edited,
        removed or inserted row shows up in verify_audit(). The hash is unkeyed: it finds accidents and edits that
        don't also rewrite every later row, not an attacker who can write the whole file."""
        at, text = _iso(now or datetime.now(UTC)), json.dumps(detail, default=str)
        own = self.db.in_transaction  # already inside a write transaction: join it
        if not own:
            self.db.execute("BEGIN IMMEDIATE")  # read the last hash and append under one lock
        try:
            last = self.db.execute("SELECT id, hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
            prev = (last["hash"] or "") if last else ""
            cur = self.db.execute(
                "INSERT INTO audit (at, kind, detail, prev_hash) VALUES (?,?,?,?)", (at, kind, text, prev)
            )
            self.db.execute(
                "UPDATE audit SET hash=? WHERE id=?", (_audit_hash(prev, cur.lastrowid, at, kind, text), cur.lastrowid)
            )
        except BaseException:
            if not own:
                self.db.rollback()
            raise
        if not own:
            self.db.commit()

    def verify_audit(self) -> int | None:
        """The ID of the first audit row that doesn't fit the chain, or None when it holds. Rows from before the
        chain existed have no hash and are skipped; once a row has one, every later row must have one that follows."""
        prev: str | None = None  # None until the first hashed row
        for row in self.db.execute("SELECT id, at, kind, detail, prev_hash, hash FROM audit ORDER BY id"):
            if prev is None and row["hash"] is None:
                continue
            link = "" if prev is None else prev
            if (row["prev_hash"] or "") != link:
                return int(row["id"])
            expect = _audit_hash(link, row["id"], row["at"], row["kind"], row["detail"])
            if row["hash"] != expect:
                return int(row["id"])
            prev = row["hash"]
        return None

    def recent_audit(self, kind: str, limit: int = 10) -> list[tuple[datetime, dict]]:
        rows = self.db.execute(
            "SELECT at, detail FROM audit WHERE kind=? ORDER BY id DESC LIMIT ?", (kind, limit)
        ).fetchall()
        return [(_dt(r["at"]), json.loads(r["detail"])) for r in rows]

    def snapshot(self, now: datetime, devices: int, online: int) -> None:
        self.db.execute("INSERT INTO snapshots (at, devices, online) VALUES (?,?,?)", (_iso(now), devices, online))
        self.db.commit()

    def last_snapshot(self) -> datetime | None:
        """When the last heartbeat read the fleet successfully."""
        row = self.db.execute("SELECT at FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        return _dt(row["at"]) if row else None

    def last_snapshot_count(self) -> int | None:
        """How many devices the last successful heartbeat read, or None before the first."""
        row = self.db.execute("SELECT devices FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        return int(row["devices"]) if row else None

    def audit_since(self, since: datetime) -> list[tuple[datetime, str, dict]]:
        rows = self.db.execute("SELECT at, kind, detail FROM audit WHERE at >= ? ORDER BY id", (_iso(since),))
        return [(_dt(r["at"]), r["kind"], json.loads(r["detail"])) for r in rows]

    def prune_snapshots(self, before: datetime) -> int:
        """Heartbeat counts pile up every few minutes; history only needs the last few months."""
        n = self.db.execute("DELETE FROM snapshots WHERE at < ?", (_iso(before),)).rowcount
        self.db.commit()
        return n

    # --- sweeps -------------------------------------------------------------------------------------
    def record_sweep(self, s: SweepResult) -> int:
        cur = self.db.execute(
            "INSERT INTO sweeps (at, devices, online, offline, behind, newly_offline, back_online, posted_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                _iso(s.at),
                s.devices,
                s.online,
                json.dumps(list(s.offline)),
                json.dumps([list(b) for b in s.behind]),
                json.dumps(list(s.newly_offline)),
                json.dumps(list(s.back_online)),
                _iso(s.posted_at),
            ),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def mark_sweep_posted(self, sweep_id: int, now: datetime) -> None:
        self.db.execute("UPDATE sweeps SET posted_at=? WHERE id=?", (_iso(now), sweep_id))
        self.db.commit()

    def last_sweep(self) -> SweepResult | None:
        row = self.db.execute("SELECT * FROM sweeps ORDER BY id DESC LIMIT 1").fetchone()
        return _sweep_from_row(row) if row else None

    def unposted_sweep(self) -> SweepResult | None:
        row = self.db.execute("SELECT * FROM sweeps WHERE posted_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
        return _sweep_from_row(row) if row else None

    def sweeps_since(self, since: datetime) -> list[SweepResult]:
        rows = self.db.execute("SELECT * FROM sweeps WHERE at >= ? ORDER BY id", (_iso(since),))
        return [_sweep_from_row(r) for r in rows]

    def snapshots_since(self, since: datetime) -> list[tuple[datetime, int, int]]:
        rows = self.db.execute(
            "SELECT at, devices, online FROM snapshots WHERE at >= ? ORDER BY at", (_iso(since),)
        ).fetchall()
        return [(_dt(r["at"]), r["devices"], r["online"]) for r in rows]


def _clean_text(text: str | None) -> str:
    """Untrusted free text (the model's reason, an error message): redacted, then capped."""
    cleaned = encodable(redact(str(text or "")))
    return cleaned if len(cleaned) <= _TEXT_CAP else cleaned[: _TEXT_CAP - 1] + "…"


def _signed(record: BoundRecord, mac: str | None) -> bool:
    """verify(), but a record too malformed to hash is simply not signed."""
    try:
        return verify(record, mac or "")
    except (ValueError, TypeError, AttributeError):
        return False


def _audit_hash(prev: str, row_id: int, at: str, kind: str, detail: str) -> str:
    return hashlib.sha256(json.dumps([prev, row_id, at, kind, detail], ensure_ascii=False).encode("utf-8")).hexdigest()


def _session_tag(page_session: str) -> str:
    """The approval page's session value may be a secret: store and log only a short hash of it."""
    return hashlib.sha256(str(page_session).encode("utf-8")).hexdigest()[:16]


def _audit_record(record: BoundRecord) -> dict[str, Any]:
    """A bound record for the audit log, with the arguments redacted. The proposals table keeps them exact."""
    return {
        "proposal_id": record.proposal_id,
        "tool": record.tool,
        "arguments": redact(parse_canonical(record.arguments)),
        "targets": list(record.targets),
        "fingerprint": record.fingerprint,
        "schema_version": record.schema_version,
        "slot": record.slot,
    }


def _finding_from_row(row: sqlite3.Row) -> Finding:
    from fleetwatch.model import Priority

    return Finding(
        key=row["key"],
        priority=Priority(row["priority"]),
        device_id=row["device_id"],
        device_name=row["device_name"],
        what=row["what"],
        impact=row["impact"] or "",
        fix=row["fix"] or "",
        fyi=bool(row["fyi"]),
    )


def _sweep_from_row(row: sqlite3.Row) -> SweepResult:
    return SweepResult(
        at=_dt(row["at"]),
        devices=row["devices"],
        online=row["online"],
        offline=tuple(json.loads(row["offline"] or "[]")),
        behind=tuple(tuple(b) for b in json.loads(row["behind"] or "[]")),
        newly_offline=tuple(json.loads(row["newly_offline"] or "[]")),
        back_online=tuple(json.loads(row["back_online"] or "[]")),
        posted_at=_dt(row["posted_at"]),
        id=row["id"],
    )


def _device_from_row(row: sqlite3.Row) -> KnownDevice:
    return KnownDevice(
        id=row["id"],
        name=row["name"] or "",
        model=row["model"] or "",
        group=row["group_name"] or "",
        online=bool(row["online"]),
        firmware=row["firmware"] or "",
        recording=bool(row["recording"]),
        last_seen=_dt(row["last_seen"]),
    )
