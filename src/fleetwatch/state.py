"""What the agent remembers between heartbeats: open findings, what was posted when, and an audit log.
SQLite, one file. Without this every heartbeat would re-post the same items."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fleetwatch.model import Finding

_SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
  key TEXT PRIMARY KEY, device_id TEXT, device_name TEXT, priority TEXT, what TEXT, fyi INTEGER,
  first_seen TEXT, last_seen TEXT, last_sent TEXT, resolved_at TEXT);
CREATE TABLE IF NOT EXISTS readiness (event_key TEXT PRIMARY KEY, posted_at TEXT, verdict TEXT);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at TEXT, kind TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY, at TEXT, devices INTEGER, online INTEGER);
"""


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
                    "INSERT OR REPLACE INTO findings VALUES (?,?,?,?,?,?,?,?,NULL,NULL)",
                    (f.key, f.device_id, f.device_name, f.priority.value, f.what, int(f.fyi), _iso(now), _iso(now)),
                )
                new.append(f)
            else:
                self.db.execute("UPDATE findings SET last_seen=? WHERE key=?", (_iso(now), f.key))
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

    def mark_readiness(self, event_key: str, verdict: str, now: datetime) -> None:
        self.db.execute("INSERT OR REPLACE INTO readiness VALUES (?,?,?)", (event_key, _iso(now), verdict))
        self.db.commit()

    # --- audit --------------------------------------------------------------------------------------
    def audit(self, kind: str, detail: dict, now: datetime | None = None) -> None:
        self.db.execute(
            "INSERT INTO audit (at, kind, detail) VALUES (?,?,?)",
            (_iso(now or datetime.now(UTC)), kind, json.dumps(detail, default=str)),
        )
        self.db.commit()

    def snapshot(self, now: datetime, devices: int, online: int) -> None:
        self.db.execute("INSERT INTO snapshots (at, devices, online) VALUES (?,?,?)", (_iso(now), devices, online))
        self.db.commit()

    def last_snapshot(self) -> datetime | None:
        """When the last heartbeat read the fleet successfully."""
        row = self.db.execute("SELECT at FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        return _dt(row["at"]) if row else None


def _finding_from_row(row: sqlite3.Row) -> Finding:
    from fleetwatch.model import Priority

    return Finding(
        key=row["key"],
        priority=Priority(row["priority"]),
        device_id=row["device_id"],
        device_name=row["device_name"],
        what=row["what"],
        fyi=bool(row["fyi"]),
    )
