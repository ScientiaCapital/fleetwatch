"""What the agent remembers between heartbeats: open findings, what was posted when, and an audit log.
SQLite, one file. Without this every heartbeat would re-post the same items."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fleetwatch.model import Finding, Fleet, Readiness, SweepResult

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
"""

# Columns added after v0.1's first schema. Old databases get them on open; new ones go through the same path.
_ADDED_COLUMNS = {
    "findings": ("impact TEXT", "fix TEXT"),
    "readiness": ("device_id TEXT", "device_name TEXT", "title TEXT", "start TEXT", "notes TEXT"),
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
    def __init__(self, path: Path | str = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
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

    # --- audit --------------------------------------------------------------------------------------
    def audit(self, kind: str, detail: dict, now: datetime | None = None) -> None:
        self.db.execute(
            "INSERT INTO audit (at, kind, detail) VALUES (?,?,?)",
            (_iso(now or datetime.now(UTC)), kind, json.dumps(detail, default=str)),
        )
        self.db.commit()

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
