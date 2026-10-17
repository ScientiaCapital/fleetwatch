"""Answer a typed question about the fleet from what Fleetwatch already knows. No AI model, no network, no tool call.

The question is untrusted text. It only picks which fixed answer to give and which known room to talk about; it is
never executed, never used as a SQL pattern, and never echoed back.
"""

import re
from dataclasses import replace
from datetime import UTC, datetime

from fleetwatch.agents.readiness.rules import check as readiness_check
from fleetwatch.i18n import at_clock, pick, short_date, t, verdict_label, vocab
from fleetwatch.model import Fleet, Priority
from fleetwatch.notes import format_note
from fleetwatch.policy import Policy
from fleetwatch.redact import scrub_text
from fleetwatch.state import KnownDevice, State

MAX_QUESTION = 300
MAX_ROOMS = 6

# English and Spanish questions. An answer is in Spanish when the question is, or when policy.yaml says `language: es`.
_OFFLINE = {"offline", "down", "desconectado", "desconectados", "desconectada", "caído", "caídos", "apagado"}
_ATTENTION = {"attention", "wrong", "broken", "problem", "problems", "issue", "issues", "fix", "problema",
              "problemas", "atención", "falla", "fallas"}  # fmt: skip
_LAST = {"last", "digest", "summary", "recap", "último", "ultimo", "resumen"}
_READY = {"ready", "listo", "lista", "listos", "preparado", "preparada"}
_FILLER = {"is", "are", "the", "a", "ready", "check", "for", "now", "how", "está", "esta", "listo", "lista", "next"}

HELP = t("en", "ask.help")  # the English text; answer() picks the language


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def _words(text: str) -> list[str]:
    return re.findall(r"[\w']+", _norm(text))


def answer(question: str, state: State, policy: Policy, fleet: Fleet | None = None, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    lang = pick(question, policy)
    q = _norm(question[:MAX_QUESTION])
    words = set(_words(q))
    if not words:
        return t(lang, "ask.help")

    named = _named_in(q, state.devices())
    if named:
        return scrub_text(_rooms(named, state, policy, fleet, now, lang))
    if words & _OFFLINE:
        return scrub_text(_offline(state, lang))
    if words & _ATTENTION:
        return scrub_text(_attention(state, lang))
    if words & _LAST:
        return scrub_text(_last_digest(state, lang))
    if words & _READY or q.startswith(("is ", "check ", "how is ", "está ", "esta ")):
        rooms = _guess_rooms(q, state)
        if not rooms:
            return t(lang, "ask.no_room")
        return scrub_text(_rooms(rooms, state, policy, fleet, now, lang))
    return t(lang, "ask.help")


def last_checked(state: State, now: datetime | None = None, lang: str = "en") -> str:
    """How fresh the answers are: when the last heartbeat read the fleet, in local time."""
    at = state.last_snapshot()
    if at is None:
        return t(lang, "ask.checked_never")
    local, today = at.astimezone(), (now or datetime.now(UTC)).astimezone()
    if local.date() == today.date():
        return t(lang, "ask.checked_today", time=f"{local:%H:%M}")
    return t(lang, "ask.checked_other", date=short_date(at, lang), time=f"{local:%H:%M}")


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
def _rooms(
    devices: list[KnownDevice], state: State, policy: Policy, fleet: Fleet | None, now: datetime, lang: str = "en"
) -> str:
    shown = devices[:MAX_ROOMS]
    blocks = [_room(d, state, policy, fleet, now, lang) for d in shown]
    if len(devices) > MAX_ROOMS:
        blocks.append(t(lang, "ask.more_rooms", n=len(devices) - MAX_ROOMS))
    return "\n\n".join(blocks)


def _room(d: KnownDevice, state: State, policy: Policy, fleet: Fleet | None, now: datetime, lang: str = "en") -> str:
    live = fleet.devices.get(d.id) if fleet else None
    event = fleet.events.get(d.id) if fleet else None
    if live is not None and event is not None and event.start >= now:
        r = readiness_check(live, event, replace(policy, language=lang), now)
        lines = [f"{d.name} · {event.title} {at_clock(lang, event.start)}: {verdict_label(r.verdict, lang)}"]
        lines += [f"- {n}" for n in r.notes]
        return "\n".join(lines + _notes(d, state, lang))

    posted = state.latest_readiness(d.id)
    open_items = [f for f in state.open_findings() if f.device_id == d.id and not f.fyi]
    head = t(lang, "ask.room.offline" if not d.online else "ask.room.online", name=d.name)
    if d.online and not open_items:
        head += t(lang, "ask.room.calm", **vocab(lang, policy.vertical))
    lines = [head]
    lines += [f"- {f.priority.label(lang)}: {f.what}" for f in sorted(open_items, key=lambda f: f.priority.rank)]
    if posted and posted.start and posted.start >= now:
        lines.append(
            t(
                lang,
                "ask.room.last_check",
                title=posted.title,
                at=at_clock(lang, posted.start),
                verdict=verdict_label(posted.verdict, lang),
            )
        )
        lines += [f"- {n}" for n in posted.notes]
    return "\n".join(lines + _notes(d, state, lang))


def _notes(d: KnownDevice, state: State, lang: str = "en") -> list[str]:
    return [t(lang, "ask.note", note=format_note(n, lang)) for n in state.notes(d.id)[:3]]


def _offline(state: State, lang: str = "en") -> str:
    down = [d for d in state.devices() if not d.online]
    if not state.devices():
        return t(lang, "ask.unseen")
    if not down:
        return t(lang, "ask.all_online")
    return "\n".join([t(lang, "ask.offline_head", n=len(down))] + [f"- {d.name}" for d in down])


def _attention(state: State, lang: str = "en") -> str:
    items = sorted((f for f in state.open_findings() if not f.fyi), key=lambda f: f.priority.rank)
    fyi = [f.what for f in state.open_findings() if f.fyi]
    if not items:
        return "\n".join([t(lang, "ask.nothing")] + fyi)
    lines: list[str] = []
    for p in Priority:
        group = [f for f in items if f.priority is p]
        if group:
            lines.append(f"{p.label(lang)} ({len(group)}):")
            lines += [f"- {f.what}" for f in group]
    return "\n".join(lines + fyi)


def _last_digest(state: State, lang: str = "en") -> str:
    recent = state.recent_audit("digest", 1)
    if not recent:
        return t(lang, "ask.no_digest")
    at, detail = recent[0]
    counts = [t(lang, f"ask.digest_{k}", n=len(detail.get(k, []))) for k in ("new", "reminders", "resolved")]
    return (
        t(lang, "ask.digest_head", at=at_clock(lang, at), counts=", ".join(counts)) + "\n\n" + _attention(state, lang)
    )
