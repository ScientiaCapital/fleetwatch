"""`fleetwatch approve --serve`: the v0.2 local page where a person approves one proposed change at a time.

See docs/design/approved-writes.md, "The approval page", "Flow" steps 3-7 and "Limits against approval fatigue".
Standard library only, like ask_page.py, but with its own handler and its own port, and never the wall-screen page.

- Listens on 127.0.0.1 only and answers only requests addressed to 127.0.0.1 or localhost on its port.
- A launch secret is made at start and printed to the console once. It's never put in a URL or a log. The first
  visit asks for it; the right secret sets an HttpOnly, SameSite=Strict session cookie that holds a random session
  ID, not the secret. Five wrong entries lock the form for a minute.
- Every POST needs that session, a token bound to the session and the proposal (an HMAC with a per-process key),
  and `Sec-Fetch-Site: same-origin` or a same-origin `Origin`. GET never changes anything.
- Every fact on a card is built by code from the stored proposal and a fresh read: the tool in plain words, each
  target's ID, current name, model and state, every argument in full, and what happens and how to undo it. The
  model's reason sits in its own box, labelled as unchecked. A card that can't be shown in full offers only Deny.
- Deny is the focused button. Approve is never focused or the default. Disruptive tools need a second step that
  names the room.
- Approve runs State.approve, State.consume, then the executor once, and shows the outcome. Starting the page
  expires every pending proposal from before.

Device, channel and event names are untrusted: they're escaped, capped, and never pick a code path.
"""

import asyncio
import hashlib
import hmac
import html
import json
import re
import secrets
import time
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Protocol
from urllib.parse import parse_qs

from fleetwatch.epiphan.executor import Outcome, room_block
from fleetwatch.model import Fleet
from fleetwatch.policy import Policy, ToolPolicy, check_arguments
from fleetwatch.proposals import BoundRecord, NotCanonical, ProposalRefused, parse_canonical, state_fingerprint
from fleetwatch.redact import redact
from fleetwatch.state import State

MAX_BODY = 4096
MAX_QUESTION = 500
FATIGUE_AFTER = 5  # approvals in one page session before the page says so
WRONG_TRIES = 5
LOCKOUT_SECONDS = 60
REASON_CAP = 500
NAME_CAP = 120
MAX_SESSIONS = 20
COOKIE = "fleetwatch_approve"
REPLAY_SLOT = "replay"  # replay proposals are bound to this slot; the real executor runs only "sandbox"

# What "shown in full" allows. Anything past these limits, or text a person can't see as written, isn't shown.
ARG_TEXT_CAP = 256
ARG_ITEMS_CAP = 20
ARG_DEPTH = 3
ARG_TOTAL_CAP = 4000
_HIDDEN = {"Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp"}  # control, format (bidi, zero-width), private, unassigned

CSP = "default-src 'none'; base-uri 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"


class Executor(Protocol):
    async def execute(self, record: BoundRecord) -> Outcome: ...


# The six proposable tools in plain words: (name, what happens, how to undo it). Written by people, never the model.
TOOL_WORDS: dict[str, dict[str, tuple[str, str, str]]] = {
    "en": {
        "batch_recording": (
            "Start or stop a recording",
            "The channel below starts or stops recording, as the action says.",
            (
                "To undo a start, stop the recording. A stopped recording can't be continued; starting again makes "
                "a new file."
            ),
        ),
        "start_stream_endpoint": (
            "Start streaming to a destination",
            "The channel starts sending video to this destination, and viewers can see it.",
            "To undo it, stop the stream.",
        ),
        "stop_stream_endpoint": (
            "Stop streaming to a destination",
            "The live stream ends for viewers now.",
            "To undo it, start the stream again. Viewers may need to reload, and the gap stays.",
        ),
        "confirm_cms_event_on_device": (
            "Confirm a scheduled event on the unit",
            "The unit may start this event at its scheduled time.",
            "There's no undo here. To stop the event, change or cancel it in the CMS.",
        ),
        "batch_reboot": (
            "Restart the unit",
            "The unit restarts and is offline for a couple of minutes. Recording and streaming on it stop.",
            "There's no undo, but the unit comes back on its own.",
        ),
        "batch_firmware_update": (
            "Firmware update",
            "The unit reboots and is offline for a few minutes.",
            "There's no undo; the old firmware isn't kept.",
        ),
    },
    "es": {
        "batch_recording": (
            "Iniciar o detener una grabación",
            "El canal indicado abajo inicia o detiene la grabación, según la acción.",
            (
                "Para deshacer un inicio, detén la grabación. Una grabación detenida no se puede continuar; al "
                "iniciar de nuevo se crea otro archivo."
            ),
        ),
        "start_stream_endpoint": (
            "Iniciar una transmisión a un destino",
            "El canal empieza a enviar video a este destino, y el público puede verlo.",
            "Para deshacerlo, detén la transmisión.",
        ),
        "stop_stream_endpoint": (
            "Detener una transmisión a un destino",
            "La transmisión en vivo termina ahora para el público.",
            (
                "Para deshacerlo, vuelve a iniciar la transmisión. Es posible que el público tenga que recargar, y "
                "el corte queda."
            ),
        ),
        "confirm_cms_event_on_device": (
            "Confirmar un evento programado en el equipo",
            "El equipo podrá iniciar este evento a la hora programada.",
            "Aquí no se puede deshacer. Para detener el evento, cámbialo o cancélalo en el CMS.",
        ),
        "batch_reboot": (
            "Reiniciar el equipo",
            (
                "El equipo se reinicia y queda sin conexión un par de minutos. La grabación y la transmisión en él "
                "se detienen."
            ),
            "No se puede deshacer, pero el equipo vuelve a conectarse solo.",
        ),
        "batch_firmware_update": (
            "Actualización de firmware",
            "El equipo se reinicia y queda sin conexión unos minutos.",
            "No se puede deshacer; el firmware anterior no se guarda.",
        ),
    },
}

TEXT: dict[str, dict[str, str]] = {
    "en": {
        "title": "Approve changes",
        "sub": "One change at a time. Nothing runs until you approve it, and Deny is always safe.",
        "secret_intro": "Type the page secret. It was printed in the console where you started this page.",
        "secret_label": "Page secret",
        "secret_button": "Open",
        "secret_wrong": "That secret isn't right.",
        "locked": "Too many wrong entries. Wait a minute, then try again.",
        "nothing": "Nothing is waiting for approval.",
        "more": "{n} more waiting after this one.",
        "tool": "Tool",
        "proposal": "Proposal",
        "devices": "Devices",
        "id": "Device ID",
        "name": "Current name",
        "model": "Model",
        "online": "Online",
        "offline": "Offline",
        "recording": "Recording",
        "not_recording": "Not recording",
        "missing": "Not on the device list just read",
        "named": "Names just read",
        "channel": "Channel",
        "stream": "Stream destination",
        "host": "Host",
        "event": "Event",
        "starts": "Starts",
        "ends": "Ends",
        "none": "none set",
        "notfound": "Not found",
        "channel_unknown": "A channel isn't on the device just read, so this can't be approved.",
        "stream_unknown": "This stream destination isn't on the team's list just read, so this can't be approved.",
        "streams_unread": "Fleetwatch couldn't read the team's stream destinations just now, so this can't be "
        "approved. Reload to try again.",
        "event_unknown": "This event isn't the one the device reports as current or next, so this can't be approved.",
        "arguments": "Every argument",
        "what": "What happens",
        "undo": "How to undo it",
        "disruptive": "This change is disruptive. After Approve, you confirm it once more.",
        "blocked_recording": "{room} is recording, so this change would be blocked.",
        "blocked_live": "{room} has an event on now, so this change would be blocked.",
        "blocked_soon": "{room} has an event that starts in {n} minutes, so this change would be blocked.",
        "blocked_soon_one": "{room} has an event that starts in 1 minute, so this change would be blocked.",
        "reason": "Written by the assistant, not checked",
        "deny": "Deny",
        "approve": "Approve",
        "cant_show": "This change can't be shown in full, so it can't be approved.",
        "cant_explain": "Fleetwatch can't describe this tool in plain words, so it can't be approved.",
        "not_rules": "These arguments don't match the tool's reviewed rules, so it can't be approved.",
        "read_failed": "Fleetwatch couldn't read the devices just now, so this can't be approved. Reload to try again.",
        "not_listed": "A device isn't on the device list just read, so this can't be approved.",
        "changed": "A device changed since this was proposed (online, recording or next event), so this can't be "
        "approved. Deny it and ask again.",
        "confirm_title": "Confirm: {tool} on {rooms}",
        "confirm_body": "This is a disruptive change. Select Confirm to run it on {rooms}. Deny stops it.",
        "confirm": "Confirm",
        "not_waiting": "This change isn't waiting for approval.",
        "cant_approve": "This change can't be approved: {why}",
        "denied": "Denied. Nothing ran.",
        "ok": "Done. {detail}",
        "error": "Epiphan reported a problem. {detail}",
        "unknown": "This change may or may not have run. {detail} Check the unit before you try anything again.",
        "refused": "Nothing ran. {detail}",
        "unused": "The approval couldn't be used, so nothing ran.",
        "lost": "The page lost track of the change after approval; it may or may not have run.",
        "fatigue": "You've approved {n} changes in this session. Read each card before you approve it.",
        "back": "Back to the next change",
        "ask_label": "Ask the assistant",
        "ask_button": "Ask",
        "ask_failed": "The assistant couldn't answer just now.",
        "footer": "Fleetwatch for Epiphan Edge · v0.2, in progress · not an officially supported Epiphan product",
    },
    "es": {
        "title": "Aprobar cambios",
        "sub": "Un cambio a la vez. Nada se ejecuta hasta que lo apruebes, y Rechazar siempre es seguro.",
        "secret_intro": "Escribe el secreto de la página. Se mostró en la consola donde iniciaste esta página.",
        "secret_label": "Secreto de la página",
        "secret_button": "Abrir",
        "secret_wrong": "Ese secreto no es correcto.",
        "locked": "Demasiados intentos incorrectos. Espera un minuto y vuelve a intentarlo.",
        "nothing": "No hay nada en espera de aprobación.",
        "more": "{n} más en espera después de este.",
        "tool": "Herramienta",
        "proposal": "Propuesta",
        "devices": "Equipos",
        "id": "ID del equipo",
        "name": "Nombre actual",
        "model": "Modelo",
        "online": "En línea",
        "offline": "Sin conexión",
        "recording": "Grabando",
        "not_recording": "Sin grabar",
        "missing": "No está en la lista de equipos recién leída",
        "named": "Nombres recién leídos",
        "channel": "Canal",
        "stream": "Destino de transmisión",
        "host": "Servidor",
        "event": "Evento",
        "starts": "Inicia",
        "ends": "Termina",
        "none": "sin definir",
        "notfound": "No encontrado",
        "channel_unknown": "Un canal no está en el equipo recién leído, así que no se puede aprobar.",
        "stream_unknown": "Este destino de transmisión no está en la lista del equipo recién leída, así que no se "
        "puede aprobar.",
        "streams_unread": "Fleetwatch no pudo leer los destinos de transmisión del equipo ahora, así que no se "
        "puede aprobar. Recarga para intentarlo de nuevo.",
        "event_unknown": "Este evento no es el que el equipo reporta como actual o siguiente, así que no se puede "
        "aprobar.",
        "arguments": "Todos los argumentos",
        "what": "Qué pasa",
        "undo": "Cómo deshacerlo",
        "disruptive": "Este cambio interrumpe el servicio. Después de Aprobar, lo confirmas una vez más.",
        "blocked_recording": "{room} está grabando, así que este cambio se bloquearía.",
        "blocked_live": "{room} tiene un evento en curso, así que este cambio se bloquearía.",
        "blocked_soon": "{room} tiene un evento que empieza en {n} minutos, así que este cambio se bloquearía.",
        "blocked_soon_one": "{room} tiene un evento que empieza en 1 minuto, así que este cambio se bloquearía.",
        "reason": "Escrito por el asistente, sin verificar",
        "deny": "Rechazar",
        "approve": "Aprobar",
        "cant_show": "Este cambio no se puede mostrar completo, así que no se puede aprobar.",
        "cant_explain": "Fleetwatch no puede describir esta herramienta con palabras simples, así que no se puede "
        "aprobar.",
        "not_rules": "Estos argumentos no coinciden con las reglas revisadas de la herramienta, así que no se puede "
        "aprobar.",
        "read_failed": "Fleetwatch no pudo leer los equipos en este momento, así que no se puede aprobar. Recarga "
        "para volver a intentarlo.",
        "not_listed": "Un equipo no está en la lista recién leída, así que no se puede aprobar.",
        "changed": "Un equipo cambió desde la propuesta (conexión, grabación o próximo evento), así que no se puede "
        "aprobar. Recházalo y vuelve a preguntar.",
        "confirm_title": "Confirmar: {tool} en {rooms}",
        "confirm_body": "Este cambio interrumpe el servicio. Selecciona Confirmar para ejecutarlo en {rooms}. "
        "Rechazar lo detiene.",
        "confirm": "Confirmar",
        "not_waiting": "Este cambio no está en espera de aprobación.",
        "cant_approve": "Este cambio no se puede aprobar: {why}",
        "denied": "Rechazado. No se ejecutó nada.",
        "ok": "Listo. {detail}",
        "error": "Epiphan informó un problema. {detail}",
        "unknown": "Es posible que este cambio se haya ejecutado o no. {detail} Revisa el equipo antes de volver a "
        "intentar algo.",
        "refused": "No se ejecutó nada. {detail}",
        "unused": "La aprobación no se pudo usar, así que no se ejecutó nada.",
        "lost": "La página perdió el rastro del cambio después de aprobarlo; es posible que se haya ejecutado o no.",
        "fatigue": "Aprobaste {n} cambios en esta sesión. Lee cada tarjeta antes de aprobarla.",
        "back": "Volver al siguiente cambio",
        "ask_label": "Pregunta al asistente",
        "ask_button": "Preguntar",
        "ask_failed": "El asistente no pudo responder en este momento.",
        "footer": "Fleetwatch for Epiphan Edge · v0.2, en desarrollo · not an officially supported Epiphan product",
    },
}

_STYLE = """
:root { --bg:#f6f8fa; --card:#fff; --fg:#1f2328; --muted:#57606a; --line:#d0d7de; --warn:#9a6700; --box:#fff8c5; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0d1117; --card:#161b22; --fg:#f0f6fc; --muted:#9198a1; --line:#30363d; --warn:#d29922;
    --box:#272115; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
  font: 17px/1.5 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 26px; margin: 0 0 4px; }
h2 { font-size: 21px; margin: 0 0 6px; }
h3 { font-size: 15px; color: var(--muted); margin: 18px 0 6px; font-weight: 600; }
p.sub, p.muted { color: var(--muted); margin: 0 0 16px; }
p.warn, p.note { color: var(--warn); font-weight: 600; }
section.card, section.outcome { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
  padding: 16px 18px; margin-top: 16px; }
section.reason { background: var(--box); border: 1px dashed var(--line); border-radius: 10px; padding: 10px 14px;
  margin-top: 18px; }
section.reason h3 { margin-top: 0; }
table { border-collapse: collapse; width: 100%; font-size: 15px; }
th, td { text-align: left; border-bottom: 1px solid var(--line); padding: 6px 8px; vertical-align: top; }
td code, p code { font: 14px ui-monospace, SFMono-Regular, Menlo, monospace; word-break: break-all; }
pre { margin:0; white-space: pre-wrap; word-wrap: break-word; font: 15px/1.5 ui-monospace, Menlo, monospace; }
div.actions { display:flex; flex-wrap:wrap; gap:12px; margin-top: 20px; }
button { font: inherit; padding: 12px 18px; border-radius: 10px; border: 1px solid var(--line);
  background: var(--card); color: var(--fg); cursor: pointer; min-height: 48px; }
button.deny { border-width: 2px; font-weight: 600; }
input[type=text], input[type=password] { font: inherit; flex: 1 1 260px; padding: 12px 14px; border-radius: 10px;
  border: 1px solid var(--line); background: var(--card); color: var(--fg); min-height: 48px; }
form.row { display:flex; flex-wrap:wrap; gap:10px; margin: 0; }
footer { color: var(--muted); font-size: 14px; margin-top: 28px; }
"""


def _e(text: Any) -> str:
    """Escape for text and for double-quoted attributes (every attribute here is double-quoted)."""
    return html.escape(str(text), quote=False).replace('"', "&quot;")


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _visible(text: str) -> str:
    """Names and models come from the fleet and are untrusted: show hidden or direction-changing characters as
    \\uXXXX so what a person reads is what's stored (the room named in the confirm step must be the real one)."""
    return "".join(
        f"\\u{ord(ch):04x}"
        if unicodedata.category(ch) in _HIDDEN or (unicodedata.category(ch) == "Zs" and ch != " ")
        else ch
        for ch in text
    )


_CHANNEL_TARGET = re.compile(r"([0-9a-f]{8,32})-([1-9][0-9]{0,2})")


def _when_text(when: Any) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC") if when else ""


def _resolved(t: dict[str, str], args: dict[str, Any], fleet: Fleet | None) -> tuple[str, str]:
    """Names for the IDs in the arguments, from the fresh read: (rows, why not). Display text only: every value is
    capped, shown with hidden characters as \\uXXXX and escaped; none is bound into the approval or picks a path.
    The arguments are already schema-checked, so the keys looked at here are the schema's own. A stream
    destination shows its name and host, never its key. An ID that isn't found says so and the card offers Deny
    only."""
    if fleet is None:  # the card already says the read failed
        return "", ""
    rows: list[str] = []
    why = ""

    def row(label: str, value: str) -> None:
        rows.append(f"<tr><th>{_e(label)}</th><td>{_e(_visible(_cap(value, NAME_CAP)))}</td></tr>")

    def gone(label: str, key: str) -> None:
        nonlocal why
        rows.append(f'<tr><th>{_e(label)}</th><td class="warn">{_e(t["notfound"])}</td></tr>')
        why = why or t[key]

    ids = args.get("device_ids") or ()
    channels = [(m.group(1), m.group(2)) for v in ids if (m := _CHANNEL_TARGET.fullmatch(str(v)))]
    if args.get("channel_id") is not None and args.get("device_id") is not None:
        channels.append((str(args["device_id"]), str(args["channel_id"])))
    for dev_id, number in channels:
        device = fleet.devices.get(dev_id)
        channel = device.channels.get(number) if device else None
        if channel is None:
            gone(f"{t['channel']} {number}", "channel_unknown")
        else:
            row(f"{t['channel']} {number}", channel.name)
    if args.get("stream_id") is not None:
        if fleet.endpoints is None:
            gone(t["stream"], "streams_unread")
        elif (endpoint := fleet.endpoints.get(str(args["stream_id"]))) is None:
            gone(t["stream"], "stream_unknown")
        else:
            row(t["stream"], endpoint.name)
            row(t["host"], endpoint.host or t["none"])
    if args.get("event_id") is not None:
        event = fleet.events.get(str(args.get("device_id")))
        if event is None or not event.id or event.id != args["event_id"]:
            gone(t["event"], "event_unknown")
        else:
            row(t["event"], event.title)
            row(t["starts"], _when_text(event.start))
            row(t["ends"], _when_text(event.end) or t["none"])
    return "".join(rows), why


def _showable_text(s: str) -> bool:
    if len(s) > ARG_TEXT_CAP or s != s.strip():
        return False
    for ch in s:
        cat = unicodedata.category(ch)
        if cat in _HIDDEN or (cat == "Zs" and ch != " "):
            return False
    return True


def showable(value: Any, depth: int = 0) -> bool:
    """True when a person can read this argument exactly as it will be sent: no hidden or look-alike space
    characters, no leading or trailing spaces, nothing too long or too deep to show."""
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, int):
        return len(str(value)) <= ARG_TEXT_CAP
    if isinstance(value, str):
        return _showable_text(value)
    if depth >= ARG_DEPTH:
        return False
    if isinstance(value, list):
        return len(value) <= ARG_ITEMS_CAP and all(showable(v, depth + 1) for v in value)
    if isinstance(value, dict):
        return len(value) <= ARG_ITEMS_CAP and all(
            isinstance(k, str) and k and _showable_text(k) and showable(v, depth + 1) for k, v in value.items()
        )
    return False


@dataclass
class Card:
    pid: int
    body: str  # the card's HTML, every value escaped
    approvable: bool
    why: str = ""  # why it can't be approved, in plain words
    disruptive: bool = False
    tool_words: str = ""
    rooms: str = ""  # escaped, for the confirm step


class ApprovePage:
    """The page's state: the launch secret, sessions, the token key, and what it needs to build cards and run one
    approved change. `read_fleet` reads the fleet fresh; `executor` runs one consumed approval; `ask_fn` answers
    the chat box (None hides it)."""

    def __init__(
        self,
        state: State,
        read_fleet: Callable[[], Awaitable[Fleet]],
        executor: Executor,
        tools: ToolPolicy,
        port: int,
        *,
        ask_fn: Callable[[str], str] | None = None,
        secret: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        policy: Policy | None = None,
    ):
        self.lead_minutes = (policy or Policy()).lead_minutes  # the readiness window, unless a tool has its own
        self.state, self.read_fleet, self.executor, self.tools, self.port = state, read_fleet, executor, tools, port
        # Chat box hook: a later PR passes the v0.2 assistant (src/fleetwatch/assistant.py) in as `ask_fn`.
        self.ask_fn = ask_fn
        self.secret = secret or secrets.token_urlsafe(32)
        self._key = secrets.token_bytes(32)  # signs per-proposal tokens; never leaves this process
        self._clock = clock
        self._sessions: dict[str, int] = {}  # session ID -> approvals in that session
        self._wrong = 0
        self._locked_until = 0.0
        self.state.expire_all_pending()  # a card from before this process can't be approved here

    # --- sessions, secret and tokens ----------------------------------------------------------------
    def new_session(self) -> str:
        while len(self._sessions) >= MAX_SESSIONS:
            self._sessions.pop(next(iter(self._sessions)))
        sid = secrets.token_urlsafe(32)
        self._sessions[sid] = 0
        return sid

    def has_session(self, sid: str | None) -> bool:
        return bool(sid) and sid in self._sessions

    def login(self, given: str) -> tuple[int, str | None]:
        """303 and a new session ID for the right secret; 403 for a wrong one; 429 while locked out."""
        now = self._clock()
        if now < self._locked_until:
            return 429, None
        if hmac.compare_digest(given.encode("utf-8"), self.secret.encode("utf-8")):
            self._wrong = 0
            return 303, self.new_session()
        self._wrong += 1
        if self._wrong >= WRONG_TRIES:
            self._wrong = 0
            self._locked_until = now + LOCKOUT_SECONDS
        return 403, None

    def token(self, purpose: str, sid: str, pid: int) -> str:
        msg = f"{purpose}|{sid}|{pid}".encode()
        return hmac.new(self._key, msg, hashlib.sha256).hexdigest()

    def _token_ok(self, purpose: str, sid: str, pid: int, given: str) -> bool:
        return hmac.compare_digest(self.token(purpose, sid, pid).encode(), given.encode("utf-8"))

    # --- cards --------------------------------------------------------------------------------------
    def _pending(self) -> list[tuple[int, BoundRecord | None, str]]:
        return self.state.pending_proposals()

    def _fresh_fleet(self) -> Fleet | None:
        try:
            return asyncio.run(self.read_fleet())
        except Exception:  # noqa: BLE001 - a failed read means the card can't be approved, nothing more
            return None

    def build_card(self, pid: int, record: BoundRecord | None, reason: str, lang: str) -> Card:
        t = TEXT[lang]
        deny_only = Card(pid, f'<p class="warn">{_e(t["cant_show"])}</p>', False, t["cant_show"])
        if record is None:
            return deny_only
        try:
            args = parse_canonical(record.arguments)
        except (NotCanonical, ValueError):
            return deny_only
        if len(record.arguments) > ARG_TOTAL_CAP or not showable(args):
            return deny_only
        words = TOOL_WORDS[lang].get(record.tool)
        if words is None:
            return Card(pid, f'<p class="warn">{_e(t["cant_explain"])}</p>', False, t["cant_explain"])
        name, what, undo = words
        why = ""
        rule = self.tools.propose.get(record.tool) if self.tools.proposable(record.tool) else None
        if rule is None or rule.schema is None or rule.schema.version != record.schema_version:
            why = t["not_rules"]
        else:
            try:
                check_arguments(record.tool, rule, args)
            except ValueError:
                why = t["not_rules"]
        disruptive = self.tools.is_disruptive(record.tool, args)

        fleet = self._fresh_fleet()
        rows, rooms = [], []
        for target in record.targets:
            device = fleet.devices.get(target) if fleet else None
            if device is None:
                rows.append(f'<tr><td><code>{_e(target)}</code></td><td colspan="4">{_e(t["missing"])}</td></tr>')
                rooms.append(target)
                continue
            dev_name = _visible(_cap(device.name, NAME_CAP))
            rooms.append(dev_name)
            rows.append(
                f"<tr><td><code>{_e(target)}</code></td><td>{_e(dev_name)}</td><td>{_e(_visible(_cap(device.model, 60)))}</td>"
                f"<td>{_e(t['online'] if device.online else t['offline'])}</td>"
                f"<td>{_e(t['recording'] if device.recording else t['not_recording'])}</td></tr>"
            )
        if not why:
            if fleet is None:
                why = t["read_failed"]
            elif any(target not in fleet.devices for target in record.targets):
                why = t["not_listed"]
            else:
                try:
                    if state_fingerprint(fleet, record.targets) != record.fingerprint:
                        why = t["changed"]
                except NotCanonical:
                    why = t["not_listed"]

        named_rows, named_why = _resolved(t, args, fleet)
        why = why or named_why

        arg_rows = "".join(
            f"<tr><th><code>{_e(k)}</code></th><td><code>{_e(json.dumps(v, ensure_ascii=False))}</code></td></tr>"
            for k, v in sorted(args.items())
        )
        clean_reason = _cap(redact(reason or ""), REASON_CAP)
        body = (
            f"<h2>{_e(name)}</h2>"
            f'<p class="muted">{_e(t["tool"])}: <code>{_e(record.tool)}</code> · {_e(t["proposal"])} {pid}</p>'
            f"<h3>{_e(t['devices'])}</h3><table><tr><th>{_e(t['id'])}</th><th>{_e(t['name'])}</th>"
            f"<th>{_e(t['model'])}</th><th>{_e(t['online'])}</th><th>{_e(t['recording'])}</th></tr>"
            + "".join(rows)
            + f"</table><h3>{_e(t['arguments'])}</h3><table>{arg_rows}</table>"
            + (f"<h3>{_e(t['named'])}</h3><table>{named_rows}</table>" if named_rows else "")
            + f"<h3>{_e(t['what'])}</h3><p>{_e(what)}</p><h3>{_e(t['undo'])}</h3><p>{_e(undo)}</p>"
            + (f'<p class="note">{_e(t["disruptive"])}</p>' if disruptive else "")
            + (self._blocked_notes(fleet, record, args, disruptive, t) if fleet else "")
            + (f'<p class="warn">{_e(why)}</p>' if why else "")
            + f'<section class="reason"><h3>{_e(t["reason"])}</h3><pre>{_e(clean_reason)}</pre></section>'
        )
        return Card(pid, body, not why, why, disruptive, name, ", ".join(rooms))

    def _blocked_notes(
        self, fleet: Fleet, record: BoundRecord, args: dict[str, Any], disruptive: bool, t: dict[str, str]
    ) -> str:
        """Information, not a gate: where the executor would refuse this change because of the room's state."""
        if not disruptive:
            return ""
        lead = timedelta(minutes=self.tools.lead_minutes(record.tool, self.lead_minutes))
        now, notes, stops = datetime.now(UTC), [], self.tools.stops_recording(record.tool, args)
        for target in record.targets:
            device = fleet.devices.get(target)
            block = room_block(device, fleet.events.get(target), now, lead, stops) if device else None
            if device is None or block is None:
                continue
            room = _visible(_cap(device.name, NAME_CAP))
            key = "blocked_soon_one" if block.kind == "soon" and block.minutes == 1 else f"blocked_{block.kind}"
            notes.append(f'<p class="warn">{_e(t[key].format(room=room, n=block.minutes))}</p>')
        return "".join(notes)

    def _card_for(self, pid: int, lang: str) -> Card | None:
        for p, record, reason in self._pending():
            if p == pid:
                return self.build_card(p, record, reason, lang)
        return None

    # --- pages --------------------------------------------------------------------------------------
    def _page(self, lang: str, inner: str, sid: str | None = None, answer: str | None = None) -> str:
        t = TEXT[lang]
        fatigue = ""
        if sid and self._sessions.get(sid, 0) >= FATIGUE_AFTER:
            fatigue = f'<p class="note">{_e(t["fatigue"].format(n=self._sessions[sid]))}</p>'
        chat = ""
        if sid and self.ask_fn is not None:  # the chat box hook; hidden when no assistant is passed in
            reply = (
                f'<section class="reason"><h3>{_e(t["reason"])}</h3><pre>{_e(answer)}</pre></section>'
                if answer is not None
                else ""
            )
            chat = (
                f"<h3>{_e(t['ask_label'])}</h3>"
                f'<form class="row" method="post" action="/ask">'
                f'<input type="hidden" name="token" value="{self.token("ask", sid, 0)}">'
                f'<input type="text" name="q" maxlength="{MAX_QUESTION}" aria-label="{_e(t["ask_label"])}"'
                f' autocomplete="off"><button type="submit">{_e(t["ask_button"])}</button></form>{reply}'
            )
        return (
            f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{_e(t['title'])}</title><style>{_STYLE}</style></head><body><main>"
            f'<h1>{_e(t["title"])}</h1><p class="sub">{_e(t["sub"])}</p>{fatigue}{inner}{chat}'
            f"<footer>{_e(t['footer'])}</footer></main></body></html>"
        )

    def render_login(self, lang: str, message: str = "") -> str:
        t = TEXT[lang]
        warn = f'<p class="warn">{_e(message)}</p>' if message else ""
        inner = (
            f'<section class="card"><p>{_e(t["secret_intro"])}</p>{warn}'
            '<form class="row" method="post" action="/login">'
            f'<input type="password" name="secret" aria-label="{_e(t["secret_label"])}" autocomplete="off" autofocus>'
            f'<button type="submit">{_e(t["secret_button"])}</button></form></section>'
        )
        return self._page(lang, inner)

    def _deny_form(self, sid: str, pid: int, lang: str) -> str:
        return (
            '<form method="post" action="/deny">'
            f'<input type="hidden" name="proposal" value="{pid}">'
            f'<input type="hidden" name="token" value="{self.token("card", sid, pid)}">'
            f'<button class="deny" type="submit" autofocus>{_e(TEXT[lang]["deny"])}</button></form>'
        )

    def render_card_page(self, sid: str, lang: str, answer: str | None = None) -> str:
        t = TEXT[lang]
        pending = self._pending()
        if not pending:
            return self._page(lang, f'<section class="card"><p>{_e(t["nothing"])}</p></section>', sid, answer)
        pid, record, reason = pending[0]
        card = self.build_card(pid, record, reason, lang)
        more = f'<p class="muted">{_e(t["more"].format(n=len(pending) - 1))}</p>' if len(pending) > 1 else ""
        approve = ""
        if card.approvable:
            approve = (
                '<form method="post" action="/approve">'
                f'<input type="hidden" name="proposal" value="{pid}">'
                f'<input type="hidden" name="token" value="{self.token("card", sid, pid)}">'
                f'<button class="approve" type="submit">{_e(t["approve"])}</button></form>'
            )
        inner = (
            f'{more}<section class="card">{card.body}'
            f'<div class="actions">{self._deny_form(sid, pid, lang)}{approve}</div></section>'
        )
        return self._page(lang, inner, sid, answer)

    def _message(self, sid: str, lang: str, text: str, extra: str = "") -> str:
        inner = (
            f'<section class="outcome"><p>{_e(text)}</p>{extra}</section>'
            f'<p><a href="/">{_e(TEXT[lang]["back"])}</a></p>'
        )
        return self._page(lang, inner, sid)

    def _confirm_page(self, sid: str, card: Card, lang: str) -> str:
        t = TEXT[lang]
        inner = (
            f'<section class="card"><h2>{_e(t["confirm_title"].format(tool=card.tool_words, rooms=card.rooms))}</h2>'
            f"<p>{_e(t['confirm_body'].format(rooms=card.rooms))}</p>{card.body}"
            f'<div class="actions">{self._deny_form(sid, card.pid, lang)}'
            '<form method="post" action="/confirm">'
            f'<input type="hidden" name="proposal" value="{card.pid}">'
            f'<input type="hidden" name="token" value="{self.token("confirm", sid, card.pid)}">'
            f'<button class="approve" type="submit">{_e(t["confirm"])}</button></form></div></section>'
        )
        return self._page(lang, inner, sid)

    def _outcome_page(self, sid: str, outcome: Outcome, lang: str) -> str:
        t = TEXT[lang]
        status = outcome.status if outcome.status in ("ok", "error", "unknown", "refused") else "unknown"
        detail = _cap(redact(str(outcome.detail)), REASON_CAP)
        extra = ""
        if outcome.errors:
            items = "".join(
                f"<li><code>{_e(_cap(redact(str(k)), NAME_CAP))}</code>: {_e(_cap(redact(str(v)), REASON_CAP))}</li>"
                for k, v in outcome.errors.items()
            )
            extra = f"<ul>{items}</ul>"
        return self._message(sid, lang, t[status].format(detail=detail), extra)

    # --- actions (every POST after the host, same-origin and session checks) -------------------------
    def handle_post(self, path: str, form: dict[str, list[str]], sid: str, lang: str) -> tuple[int, str]:
        t = TEXT[lang]
        token = (form.get("token") or [""])[0]
        if path == "/ask":
            if self.ask_fn is None:
                return 404, "Not found"
            if not self._token_ok("ask", sid, 0, token):
                return 403, "Forbidden"
            q = (form.get("q") or [""])[0][:MAX_QUESTION]
            try:
                answer = redact(str(self.ask_fn(q)))
            except Exception:  # noqa: BLE001 - the chat box never breaks the approval page
                answer = t["ask_failed"]
            return 200, self.render_card_page(sid, lang, answer)
        if path not in ("/approve", "/confirm", "/deny"):
            return 404, "Not found"
        raw = (form.get("proposal") or [""])[0]
        if not (raw.isascii() and raw.isdigit() and len(raw) <= 18):
            return 403, "Forbidden"
        pid = int(raw)
        if not self._token_ok("confirm" if path == "/confirm" else "card", sid, pid, token):
            return 403, "Forbidden"
        if path == "/deny":
            if self.state.deny(pid, sid):
                return 200, self._message(sid, lang, t["denied"])
            return 409, self._message(sid, lang, t["not_waiting"])
        card = self._card_for(pid, lang)  # a fresh read, again
        if card is None:
            return 409, self._message(sid, lang, t["not_waiting"])
        if not card.approvable:
            return 409, self._message(sid, lang, t["cant_approve"].format(why=card.why))
        if path == "/approve" and card.disruptive:
            return 200, self._confirm_page(sid, card, lang)
        return self._run(pid, sid, lang)

    def _run(self, pid: int, sid: str, lang: str) -> tuple[int, str]:
        t = TEXT[lang]
        try:
            approval_id = self.state.approve(pid, sid)
        except ProposalRefused:
            return 409, self._message(sid, lang, t["not_waiting"])
        self._sessions[sid] = self._sessions.get(sid, 0) + 1
        record = self.state.consume(approval_id)
        if record is None:
            return 409, self._message(sid, lang, t["unused"])
        try:
            outcome = asyncio.run(self.executor.execute(record))
        except Exception:  # noqa: BLE001 - the write may have been sent; never say it didn't
            outcome = Outcome("unknown", t["lost"])
        return 200, self._outcome_page(sid, outcome, lang)


class RecordingExecutor:
    """For replay and demos. Takes a consumed record the way WriteExecutor does (claimed once), records the tool and
    its canonical arguments, and sends nothing anywhere: it has no sign-in, no MCP session and no network code."""

    def __init__(self, state: State):
        self.state = state
        self.calls: list[tuple[str, bytes]] = []

    async def execute(self, record: BoundRecord) -> Outcome:
        approval_id = self.state.claim(record)
        if approval_id is None:
            return Outcome("refused", "Nothing ran: this isn't an approved change waiting to run.")
        self.calls.append((record.tool, record.arguments))
        self.state.record_outcome(approval_id, "ok", "replay: recorded; nothing was sent")
        self.state.audit("replay_execution", {"proposal_id": record.proposal_id, "tool": record.tool})
        return Outcome("ok", "Replay: the change was recorded. Nothing was sent to a device.")


_MASTER_ID = re.compile(r"[0-9a-f]{8,32}")


def seed_replay_sample(state: State, tools: ToolPolicy, fleet: Fleet) -> int | None:
    """Replay only: one sample proposal (restart the first online unit in the sample) so the demo has a card. It's
    bound to the replay slot, which the real executor refuses."""
    rule = tools.propose.get("batch_reboot")
    if rule is None or rule.schema is None:
        return None
    target = next((d.id for d in fleet.devices.values() if d.online and _MASTER_ID.fullmatch(d.id)), None)
    if target is None:
        return None
    args = {"device_ids": [target]}
    check_arguments("batch_reboot", rule, args)
    return state.add_proposal(
        "batch_reboot",
        args,
        [target],
        state_fingerprint(fleet, [target]),
        rule.schema.version,
        REPLAY_SLOT,
        "Sample proposal for the replay demo. This unit looks stuck.",
    )


def _language(header: str | None) -> str:
    first = (header or "").split(",")[0].strip().lower()
    return "es" if first.startswith("es") else "en"


def make_handler(page: ApprovePage):
    allowed_hosts = {f"127.0.0.1:{page.port}", f"localhost:{page.port}"}
    allowed_origins = {f"http://{h}" for h in allowed_hosts}

    class Handler(BaseHTTPRequestHandler):
        server_version = "fleetwatch"
        timeout = 10  # seconds: a client that stalls mid-request can't freeze the single-threaded page
        sys_version = ""

        def log_message(self, *_):  # nothing about requests goes to a log
            pass

        def _send(
            self, status: int, body: str, content_type: str = "text/html; charset=utf-8", extra: dict | None = None
        ) -> None:
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _plain(self, status: int, text: str) -> None:
            self._send(status, text, "text/plain; charset=utf-8")

        def _host_ok(self) -> bool:
            if self.headers.get("Host", "") in allowed_hosts:
                return True
            self._plain(403, "Forbidden")
            return False

        def _same_origin(self) -> bool:
            site, origin = self.headers.get("Sec-Fetch-Site"), self.headers.get("Origin")
            if origin is not None and origin not in allowed_origins:
                return False
            if site is not None:
                return site == "same-origin"
            return origin is not None

        def _session(self) -> str | None:
            try:
                jar = SimpleCookie(self.headers.get("Cookie", ""))
            except CookieError:
                return None
            morsel = jar.get(COOKIE)
            sid = morsel.value if morsel else None
            return sid if page.has_session(sid) else None

        def do_GET(self):
            if not self._host_ok():
                return
            if self.path.split("?", 1)[0] != "/":
                self._plain(404, "Not found")
                return
            lang = _language(self.headers.get("Accept-Language"))
            sid = self._session()
            self._send(200, page.render_card_page(sid, lang) if sid else page.render_login(lang))

        def do_POST(self):
            if not self._host_ok():
                return
            raw = (self.headers.get("Content-Length") or "0").strip()
            if not (raw.isascii() and raw.isdigit()):
                self._plain(400, "Bad request")
                return
            length = int(raw)
            if length > MAX_BODY:
                self._plain(413, "Too long")
                return
            form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
            if not self._same_origin():
                self._plain(403, "Forbidden")
                return
            lang = _language(self.headers.get("Accept-Language"))
            if self.path == "/login":
                code, sid = page.login((form.get("secret") or [""])[0])
                if code == 303 and sid:
                    cookie = f"{COOKIE}={sid}; HttpOnly; SameSite=Strict; Path=/"
                    self._send(303, "", extra={"Location": "/", "Set-Cookie": cookie})
                    return
                message = TEXT[lang]["locked" if code == 429 else "secret_wrong"]
                self._send(code, page.render_login(lang, message))
                return
            sid = self._session()
            if sid is None:
                self._plain(403, "Forbidden")
                return
            code, body = page.handle_post(self.path, form, sid, lang)
            if body in ("Forbidden", "Not found"):
                self._plain(code, body)
            else:
                self._send(code, body)

    return Handler


def serve(page: ApprovePage) -> None:
    httpd = HTTPServer(("127.0.0.1", page.port), make_handler(page))
    print(f"Approve changes (v0.2): http://127.0.0.1:{page.port}/  (Ctrl+C to stop)")
    print(f"Page secret, shown only here and only once. Type it on the page: {page.secret}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
