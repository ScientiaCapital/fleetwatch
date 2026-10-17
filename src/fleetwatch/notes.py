"""Per-room notes: `fleetwatch note "<room>" "<text>"` and `fleetwatch notes [room | --search text]`.

A note is text a person typed, so it is untrusted like a device name: it is redacted, stripped of control
characters and capped before it is stored, and it is only ever shown, never matched against or acted on. Notes live
in the local state DB; adding one never signs in or calls Epiphan.
"""

import getpass
import unicodedata
from datetime import datetime

from fleetwatch.i18n import short_date, t
from fleetwatch.redact import redact
from fleetwatch.state import KnownDevice, Note, State

MAX_NOTE = 280
MAX_AUTHOR = 40
MAX_CANDIDATES = 10


def _clean(text: str, cap: int) -> str:
    # Whitespace controls (newlines, tabs) become one space; every other control or format character
    # (escape sequences, NUL, bidi overrides, zero-width marks) is dropped.
    kept = "".join(ch for ch in text if ch.isspace() or not unicodedata.category(ch).startswith("C"))
    one_line = " ".join(kept.split())
    return str(redact(one_line))[:cap].rstrip()


def clean_note(text: str) -> str:
    return _clean(text, MAX_NOTE)


def clean_author(text: str) -> str:
    return _clean(text, MAX_AUTHOR) or "unknown"


def default_author() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001  (no login name in some containers)
        return "unknown"


def format_note(n: Note, lang: str = "en") -> str:
    """One line for the digest and `ask`: the note in quotes, then who and when."""
    return f"“{n.note}” ({n.author}, {short_date(n.at, lang)})"


def add_note(state: State, room: str, text: str, author: str, now: datetime, lang: str = "en") -> tuple[int, str]:
    """Save a note against exactly one known room. Returns (exit code, message for the person)."""
    note = clean_note(text)
    if not note:
        return 1, t(lang, "notes.empty")
    if not state.devices():
        return 1, t(lang, "ask.unseen")
    matches = state.find_devices(room)
    if not matches:
        return 1, t(lang, "notes.no_room")
    if len(matches) > 1:
        lines = [t(lang, "notes.many_rooms")]
        lines += [f"- {d.name}" for d in matches[:MAX_CANDIDATES]]
        if len(matches) > MAX_CANDIDATES:
            lines.append(t(lang, "notes.and_more", n=len(matches) - MAX_CANDIDATES))
        return 1, "\n".join(lines)
    (device,) = matches
    state.add_note(device.id, note, clean_author(author), now)
    return 0, t(lang, "notes.saved", name=device.name)


def list_notes(state: State, room: str | None = None, search: str | None = None, lang: str = "en") -> str:
    """Newest first. `room` narrows to the rooms it matches; `search` is a plain case-insensitive substring."""
    names = {d.id: d.name for d in state.devices()}
    notes = state.notes()
    if room:
        rooms: list[KnownDevice] = state.find_devices(room)
        if not rooms:
            return t(lang, "notes.no_room_short")
        ids = {d.id for d in rooms}
        notes = [n for n in notes if n.device_id in ids]
    if search:
        needle = " ".join(search.split()).casefold()
        notes = [n for n in notes if needle in n.note.casefold()]
    if not notes:
        return t(lang, "notes.no_match") if (room or search) else t(lang, "notes.none")
    return "\n".join(
        f"{short_date(n.at, lang)} {n.at.astimezone():%-I:%M %p} · {names.get(n.device_id, n.device_id)} · {n.author}: {n.note}"
        for n in notes
    )
