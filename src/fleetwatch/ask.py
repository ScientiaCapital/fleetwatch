"""Answer a typed question about the fleet from what Fleetwatch already knows. No AI model, no network, no tool call.

The question is untrusted text. It only picks which fixed answer to give and which known room to talk about; it is
never executed, never used as a SQL pattern, and never echoed back.
"""

import re
from datetime import UTC, datetime

from fleetwatch.agents.readiness.rules import check as readiness_check
from fleetwatch.model import Fleet, Priority
from fleetwatch.policy import Policy
from fleetwatch.redact import scrub_text
from fleetwatch.state import KnownDevice, State

MAX_QUESTION = 300
MAX_ROOMS = 6

# English and Spanish, since the first booth is in Latin America. Answers stay in English like the digest.
_OFFLINE = {"offline", "down", "desconectado", "desconectados", "desconectada", "caído", "caídos", "apagado"}
_ATTENTION = {"attention", "wrong", "broken", "problem", "problems", "issue", "issues", "fix", "problema",
              "problemas", "atención", "falla", "fallas"}  # fmt: skip
_LAST = {"last", "digest", "summary", "recap", "último", "ultimo", "resumen"}
_READY = {"ready", "listo", "lista", "listos", "preparado", "preparada"}
_FILLER = {"is", "are", "the", "a", "ready", "check", "for", "now", "how", "está", "esta", "listo", "lista", "next"}

HELP = (
    "I can answer: what needs attention, what's offline, the last digest, "
    "and whether a room is ready (for example: is Main Stage ready)."
)
_ORDER = {Priority.FIX_FIRST: 0, Priority.FIX_SOON: 1, Priority.WHEN_CONVENIENT: 2}


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def _words(text: str) -> list[str]:
    return re.findall(r"[\w']+", _norm(text))


def answer(question: str, state: State, policy: Policy, fleet: Fleet | None = None, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    q = _norm(question[:MAX_QUESTION])
    words = set(_words(q))
    if not words:
        return HELP

    named = _named_in(q, state.devices())
    if named:
        return scrub_text(_rooms(named, state, policy, fleet, now))
    if words & _OFFLINE:
        return scrub_text(_offline(state))
    if words & _ATTENTION:
        return scrub_text(_attention(state))
    if words & _LAST:
        return scrub_text(_last_digest(state))
    if words & _READY or q.startswith(("is ", "check ", "how is ", "está ", "esta ")):
        rooms = _guess_rooms(q, state)
        if not rooms:
            return "I couldn't find that room. Ask what's offline to see room names, or use the exact name."
        return scrub_text(_rooms(rooms, state, policy, fleet, now))
    return HELP


# --- which room ---------------------------------------------------------------------------------------------
def _named_in(q: str, devices: list[KnownDevice]) -> list[KnownDevice]:
    """Devices whose whole name appears in the question. A name inside a longer matched name doesn't count."""
    hits = [d for d in devices if _norm(d.name) and re.search(rf"(?<!\w){re.escape(_norm(d.name))}(?!\w)", q)]
    names = {_norm(d.name) for d in hits}
    return [d for d in hits if not any(_norm(d.name) != n and _norm(d.name) in n for n in names)]


def _guess_rooms(q: str, state: State) -> list[KnownDevice]:
    rest = " ".join(w for w in _words(q) if w not in _FILLER)
    if not rest:
        return []
    direct = state.find_devices(rest)
    if direct:
        return direct
    devices = state.devices()
    names = {d.id: set(_words(d.name)) for d in devices}
    # Words most devices share ("room", "pearl") don't tell rooms apart.
    common = {w for w in set().union(*names.values()) if sum(w in n for n in names.values()) > len(devices) / 2}
    asked = set(rest.split()) - common
    numbers = {w for w in asked if any(ch.isdigit() for ch in w)}  # "room 999" must not match "Room 110"
    scores = {d.id: len(asked & names[d.id]) if numbers <= names[d.id] else 0 for d in devices}
    best = max(scores.values(), default=0)
    return [d for d in devices if best and scores[d.id] == best]


# --- answers ------------------------------------------------------------------------------------------------
def _when(dt: datetime) -> str:
    return dt.astimezone().strftime("%-I:%M %p")


def _rooms(devices: list[KnownDevice], state: State, policy: Policy, fleet: Fleet | None, now: datetime) -> str:
    shown = devices[:MAX_ROOMS]
    blocks = [_room(d, state, policy, fleet, now) for d in shown]
    if len(devices) > MAX_ROOMS:
        blocks.append(f"{len(devices) - MAX_ROOMS} more rooms match. Use the full name to narrow it down.")
    return "\n\n".join(blocks)


def _room(d: KnownDevice, state: State, policy: Policy, fleet: Fleet | None, now: datetime) -> str:
    live = fleet.devices.get(d.id) if fleet else None
    event = fleet.events.get(d.id) if fleet else None
    if live is not None and event is not None and event.start >= now:
        r = readiness_check(live, event, policy)
        lines = [f"{d.name} · {event.title} at {_when(event.start)}: {r.verdict}"]
        lines += [f"- {n}" for n in r.notes]
        return "\n".join(lines)

    posted = state.latest_readiness(d.id)
    open_items = [f for f in state.open_findings() if f.device_id == d.id and not f.fyi]
    head = f"{d.name}: offline" if not d.online else f"{d.name}: online"
    if d.online and not open_items:
        head += f", nothing needs attention. No {policy.event_word} is coming up that I know of."
    lines = [head]
    lines += [f"- {f.priority.value}: {f.what}" for f in sorted(open_items, key=lambda f: _ORDER[f.priority])]
    if posted and posted.start and posted.start >= now:
        lines.append(f"Last check: {posted.title} at {_when(posted.start)}: {posted.verdict}")
        lines += [f"- {n}" for n in posted.notes]
    return "\n".join(lines)


def _offline(state: State) -> str:
    down = [d for d in state.devices() if not d.online]
    if not state.devices():
        return "I haven't seen the fleet yet. Run a heartbeat first."
    if not down:
        return "Everything is online."
    head = f"{len(down)} offline:" if len(down) > 1 else "1 offline:"
    return "\n".join([head] + [f"- {d.name}" for d in down])


def _attention(state: State) -> str:
    items = sorted((f for f in state.open_findings() if not f.fyi), key=lambda f: _ORDER[f.priority])
    fyi = [f.what for f in state.open_findings() if f.fyi]
    if not items:
        return "\n".join(["Nothing needs attention right now."] + fyi)
    lines: list[str] = []
    for p in Priority:
        group = [f for f in items if f.priority is p]
        if group:
            lines.append(f"{p.value} ({len(group)}):")
            lines += [f"- {f.what}" for f in group]
    return "\n".join(lines + fyi)


def _last_digest(state: State) -> str:
    recent = state.recent_audit("digest", 1)
    if not recent:
        return "No digest has been posted yet."
    at, detail = recent[0]
    counts = [f"{len(detail.get(k, []))} {label}" for k, label in
              (("new", "new"), ("reminders", "reminders"), ("resolved", "back to normal"))]  # fmt: skip
    return f"Last digest at {_when(at)}: {', '.join(counts)}.\n\n" + _attention(state)
