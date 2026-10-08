"""Binding for v0.2 proposals: one canonical form for arguments, and a keyed hash over everything a person approves.

See docs/design/approved-writes.md, "Binding". This module stores and checks; it can't run anything. Nothing here
calls Epiphan.

Canonical form (`canonical`):

- The arguments are one JSON object.
- Keys are sorted, at every level. No whitespace: separators are "," and ":".
- UTF-8 bytes, with non-ASCII text written as itself (`ensure_ascii=False`), not as \\u escapes.
- Integers stay integers, at any size. Floats are refused, and so are NaN, Infinity and -Infinity, because the
  same number can be written more than one way.
- When the arguments arrive as JSON text, a duplicate key anywhere is refused, not silently resolved.
- Only dicts with string keys, lists, strings, integers, booleans and null are allowed.
- The caller fills in schema defaults before calling, so a default and an explicit value hash the same.

The same function makes the form at proposal time and at run time, so what was approved is byte for byte what runs.

The keyed hash (HMAC-SHA256) covers the proposal ID, the tool, the canonical arguments, the sorted target device
IDs, the state fingerprint, the schema version and the sign-in slot. Its secret is made when the process starts
and is only ever held in memory, so a restarted process can't vouch for an old approval.
"""

import hashlib
import hmac
import json
import math
import secrets
from dataclasses import dataclass
from typing import Any

_SECRET = secrets.token_bytes(32)

FINGERPRINT_FIELDS = ("next_event_start", "online", "recording")


class NotCanonical(ValueError):
    """The arguments or fingerprint can't be put in canonical form, so they can't be bound."""


class ProposalRefused(Exception):
    """A proposal, approval or limit check said no. `reason` is plain words for the operator and the audit log."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise NotCanonical(f"duplicate key in arguments: {key!r}")
        out[key] = value
    return out


def _refuse_float(text: str) -> Any:
    raise NotCanonical(f"floats aren't allowed in arguments: {text}")


def _refuse_constant(text: str) -> Any:
    raise NotCanonical(f"{text} isn't allowed in arguments")


def parse_canonical(text: str | bytes) -> dict[str, Any]:
    """Parse JSON arguments strictly: no duplicate keys, no floats, no NaN or Infinity, and an object at the top."""
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    value = json.loads(
        text, object_pairs_hook=_no_duplicates, parse_float=_refuse_float, parse_constant=_refuse_constant
    )
    if not isinstance(value, dict):
        raise NotCanonical("arguments must be a JSON object")
    return value


def _check(value: Any, path: str = "arguments") -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        what = "NaN or Infinity" if not math.isfinite(value) else "floats"
        raise NotCanonical(f"{what} aren't allowed in arguments ({path})")
    if isinstance(value, list):
        for i, item in enumerate(value):
            _check(item, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise NotCanonical(f"argument keys must be strings ({path})")
            _check(item, f"{path}.{key}")
        return
    raise NotCanonical(f"{type(value).__name__} isn't allowed in arguments ({path})")


def canonical(arguments: dict[str, Any] | str | bytes) -> bytes:
    """The one canonical form of a tool's arguments, as UTF-8 bytes. See the module docstring for the rules."""
    if isinstance(arguments, (str, bytes)):
        arguments = parse_canonical(arguments)
    if not isinstance(arguments, dict):
        raise NotCanonical("arguments must be a JSON object")
    _check(arguments)
    text = json.dumps(arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return text.encode("utf-8")


def normalize_fingerprint(targets: tuple[str, ...], fingerprint: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Check the fingerprint has exactly one entry per target, each with exactly online, recording and
    next_event_start, of the right types. Raises ValueError otherwise."""
    if set(fingerprint) != set(targets):
        raise NotCanonical("the state fingerprint must cover exactly the resolved targets")
    out: dict[str, dict[str, Any]] = {}
    for target in targets:
        entry = fingerprint[target]
        if not isinstance(entry, dict) or set(entry) != set(FINGERPRINT_FIELDS):
            raise NotCanonical(f"fingerprint for {target!r} needs exactly {', '.join(FINGERPRINT_FIELDS)}")
        if not isinstance(entry["online"], bool) or not isinstance(entry["recording"], bool):
            raise NotCanonical(f"fingerprint for {target!r}: online and recording must be true or false")
        if entry["next_event_start"] is not None and not isinstance(entry["next_event_start"], str):
            raise NotCanonical(f"fingerprint for {target!r}: next_event_start must be text or null")
        out[target] = {k: entry[k] for k in FINGERPRINT_FIELDS}
    return out


@dataclass(frozen=True)
class BoundRecord:
    """Everything a person approves. `arguments` is the canonical bytes; `targets` are device IDs, sorted."""

    proposal_id: int
    tool: str
    arguments: bytes
    targets: tuple[str, ...]
    fingerprint: dict[str, dict[str, Any]]
    schema_version: int
    slot: str

    def message(self) -> bytes:
        """The exact bytes the HMAC covers. The arguments go in as their canonical text, so nothing re-orders them."""
        return canonical(
            {
                "arguments": self.arguments.decode("utf-8"),
                "fingerprint": self.fingerprint,
                "proposal_id": self.proposal_id,
                "schema_version": self.schema_version,
                "slot": self.slot,
                "targets": list(self.targets),
                "tool": self.tool,
                "v": 1,
            }
        )


def sign(record: BoundRecord, key: bytes | None = None) -> str:
    """HMAC-SHA256 of the bound record, hex. `key` defaults to this process's secret."""
    return hmac.new(_SECRET if key is None else key, record.message(), hashlib.sha256).hexdigest()


def verify(record: BoundRecord, mac: str, key: bytes | None = None) -> bool:
    return hmac.compare_digest(sign(record, key), mac or "")
