"""Plain-language messages in the Edge Claude Kit's calm tone. Templates only: no alarm words, no codes,
priorities in words. No AI model is involved."""

from fleetwatch.i18n import t, verdict_label, verdict_now_label
from fleetwatch.model import Finding, Readiness
from fleetwatch.notes import format_note
from fleetwatch.state import Note


def _line(f: Finding, lang: str = "en") -> str:
    parts = [f"*{f.priority.label(lang)}*: {f.what}."]
    if f.impact:
        parts.append(f"{f.impact}.")
    if f.fix:
        parts.append(f"_{f.fix}._")
    return " ".join(parts)


def render_digest(
    new: list[Finding],
    reminders: list[Finding],
    resolved: list[Finding],
    *,
    first_run: bool = False,
    notes: dict[str, list[Note]] | None = None,
    lang: str = "en",
) -> str | None:
    """None means nothing to say. Storage FYIs ride along only when there is something else to post,
    or on the first run. A room's notes show once, quoted, under its first finding."""
    notes, shown = notes or {}, set()

    def with_notes(f: Finding) -> list[str]:
        out = [f"• {_line(f, lang)}"]
        if f.device_id in notes and f.device_id not in shown:
            shown.add(f.device_id)
            out += [f"  ↳ {format_note(n)}" for n in notes[f.device_id]]
        return out

    items = sorted((f for f in new if not f.fyi), key=lambda f: f.priority.rank)
    fyi = [f for f in new if f.fyi]
    lines: list[str] = []
    if items:
        lines.append(f"*{t(lang, 'digest.needs_attention' if not first_run else 'digest.fleet_check')}*")
        lines += [line for f in items for line in with_notes(f)]
    if reminders:
        lines.append(f"*{t(lang, 'digest.still_open')}*")
        lines += [line for f in sorted(reminders, key=lambda f: f.priority.rank) for line in with_notes(f)]
    if resolved:
        lines.append(f"*{t(lang, 'digest.back_to_normal')}*")
        lines += [f"• {f.what}" for f in resolved]
    if first_run and not items and not reminders:
        lines.append(t(lang, "digest.all_clear"))
    if lines and fyi:
        lines += [f.what for f in fyi]
    return "\n".join(lines) if lines else None


def render_readiness(r: Readiness, was: str | None = None, lang: str = "en") -> str:
    """The check before an event. `was` is the verdict posted earlier for the same event, when this is a change."""
    start = r.event.start.astimezone()
    when = start.strftime("%-I:%M %p")
    at = t(lang, "readiness.at_one" if start.hour % 12 == 1 else "readiness.at", when=when)
    head = f"*{r.device_name}* · {r.event.title} {at}: "
    if was is None:
        head += f"*{verdict_label(r.verdict, lang)}*"
    else:
        head += f"*{verdict_now_label(r.verdict, lang)}* " + t(lang, "readiness.was", was=verdict_label(was, lang))
    return head if not r.notes else head + "\n" + "\n".join(f"• {n}" for n in r.notes)
