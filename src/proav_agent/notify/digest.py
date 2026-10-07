"""Plain-language messages in the Edge Claude Kit's calm tone. Templates only: no alarm words, no codes,
priorities in words. An optional LLM polish can rewrite these later; the templates are always the fallback."""

from proav_agent.model import Finding, Priority, Readiness

_ORDER = {Priority.FIX_FIRST: 0, Priority.FIX_SOON: 1, Priority.WHEN_CONVENIENT: 2}


def _line(f: Finding) -> str:
    parts = [f"*{f.priority.value}*: {f.what}."]
    if f.impact:
        parts.append(f"{f.impact}.")
    if f.fix:
        parts.append(f"_{f.fix}._")
    return " ".join(parts)


def render_digest(
    new: list[Finding], reminders: list[Finding], resolved: list[Finding], *, first_run: bool = False
) -> str | None:
    """None means nothing to say. Storage FYIs ride along only when there is something else to post,
    or on the first run."""
    items = sorted((f for f in new if not f.fyi), key=lambda f: _ORDER[f.priority])
    fyi = [f for f in new if f.fyi]
    lines: list[str] = []
    if items:
        lines.append("*Needs attention*" if not first_run else "*Fleet check*")
        lines += [f"• {_line(f)}" for f in items]
    if reminders:
        lines.append("*Still open*")
        lines += [f"• {_line(f)}" for f in sorted(reminders, key=lambda f: _ORDER[f.priority])]
    if resolved:
        lines.append("*Back to normal*")
        lines += [f"• {f.what}" for f in resolved]
    if first_run and not items and not reminders:
        lines.append("All clear. Nothing needs attention right now.")
    if lines and fyi:
        lines += [f.what for f in fyi]
    return "\n".join(lines) if lines else None


def render_readiness(r: Readiness) -> str:
    when = r.event.start.astimezone().strftime("%-I:%M %p")
    head = f"*{r.device_name}* · {r.event.title} at {when}: *{r.verdict}*"
    return head if not r.notes else head + "\n" + "\n".join(f"• {n}" for n in r.notes)
