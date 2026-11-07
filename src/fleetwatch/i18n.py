"""English and Spanish for what people read: findings, the digest and the before-event lines.

Stored values stay English whatever the language: priorities (`Priority.value`), verdicts (`READY` and the rest) and
finding keys. Only the text shown changes, so switching `language` in policy.yaml never reposts or reopens anything.
Spanish is Mexican Spanish, informal (tú), like README.es.md.

`t()` fills one message. `say()` also fills the words for a scheduled recording ({event}, {Events}, ...) from the
policy's `vertical`, because Spanish needs the article and the gender that go with each one.
"""

import re
from typing import Any

LANGUAGES = ("en", "es")

PRIORITY: dict[str, dict[str, str]] = {
    "en": {"Fix first": "Fix first", "Fix soon": "Fix soon", "When convenient": "When convenient"},
    "es": {"Fix first": "Arreglar primero", "Fix soon": "Arreglar pronto", "When convenient": "Cuando convenga"},
}

VERDICT: dict[str, dict[str, str]] = {
    "en": {"Ready": "Ready", "Ready, with notes": "Ready, with notes", "Not ready": "Not ready"},
    "es": {"Ready": "Listo", "Ready, with notes": "Listo, con notas", "Not ready": "No está listo"},
}

VERDICT_NOW: dict[str, dict[str, str]] = {
    "en": {"Ready": "Ready now", "Ready, with notes": "Ready now, with notes", "Not ready": "Now not ready"},
    "es": {
        "Ready": "Ya está listo",
        "Ready, with notes": "Ya está listo, con notas",
        "Not ready": "Ahora no está listo",
    },
}

# The words for a scheduled recording, per vertical: event, Events (capitalized, with its article in Spanish),
# the_event, the_scheduled and Next (capitalized). Spanish needs all of them for gender and number.
_VOCAB: dict[str, dict[str, dict[str, str]]] = {
    "en": {
        v: {
            "event": one,
            "Events": many.capitalize(),
            "the_event": f"the {one}",
            "the_scheduled": f"the scheduled {one}",
            "Next": f"The next {one}",
            "NoEvent": f"No {one}",
        }
        for v, (one, many) in {
            "events": ("event", "events"),
            "education": ("class", "classes"),
            "business": ("meeting", "meetings"),
            "courts": ("hearing", "hearings"),
            "worship": ("service", "services"),
        }.items()
    },
    "es": {
        "events": {
            "event": "evento",
            "Events": "Los eventos",
            "the_event": "el evento",
            "the_scheduled": "el evento programado",
            "Next": "El próximo evento",
            "NoEvent": "Ningún evento",
        },
        "education": {
            "event": "clase",
            "Events": "Las clases",
            "the_event": "la clase",
            "the_scheduled": "la clase programada",
            "Next": "La próxima clase",
            "NoEvent": "Ninguna clase",
        },
        "business": {
            "event": "reunión",
            "Events": "Las reuniones",
            "the_event": "la reunión",
            "the_scheduled": "la reunión programada",
            "Next": "La próxima reunión",
            "NoEvent": "Ninguna reunión",
        },
        "courts": {
            "event": "audiencia",
            "Events": "Las audiencias",
            "the_event": "la audiencia",
            "the_scheduled": "la audiencia programada",
            "Next": "La próxima audiencia",
            "NoEvent": "Ninguna audiencia",
        },
        "worship": {
            "event": "servicio",
            "Events": "Los servicios",
            "the_event": "el servicio",
            "the_scheduled": "el servicio programado",
            "Next": "El próximo servicio",
            "NoEvent": "Ningún servicio",
        },
    },
}

MESSAGES: dict[str, dict[str, str]] = {
    # --- scanner findings: what / impact / fix -----------------------------------------------------------------
    "scan.camera_offline.what": {"en": "{name} is offline", "es": "{name} está sin conexión"},
    "scan.camera_offline.impact": {
        "en": "Its picture may be missing from the Pearl channels that use it, and Edge can't control it",
        "es": "Su imagen puede faltar en los canales Pearl que la usan, y Edge no puede controlarla",
    },
    "scan.camera_offline.fix": {
        "en": "Check power (PoE) and the network cable at the camera",
        "es": "Revisa la alimentación (PoE) y el cable de red de la cámara",
    },
    "scan.offline_recording.what": {
        "en": "{name} is offline but still shows {channels} recording",
        "es": "{name} está sin conexión, pero todavía aparece grabando en {channels}",
    },
    "scan.offline_recording.impact": {
        "en": "Edge can't see or stop that recording, and it won't upload until the unit is back",
        "es": "Edge no puede ver ni detener esa grabación, y no se subirá hasta que la unidad vuelva",
    },
    "scan.offline_recording.fix": {
        "en": "Check power and network at the unit",
        "es": "Revisa la alimentación y la red de la unidad",
    },
    "scan.offline.what": {"en": "{name} is offline", "es": "{name} está sin conexión"},
    "scan.offline.impact": {
        "en": "{Events} in that room won't record or stream until it's back",
        "es": "{Events} en esa sala no se grabarán ni se transmitirán hasta que vuelva",
    },
    "scan.offline.fix": {
        "en": "Check power and the network cable at the unit",
        "es": "Revisa la alimentación y el cable de red de la unidad",
    },
    "scan.no_signal.what": {"en": "No picture on {channel} in {name}", "es": "Sin imagen en {channel} de {name}"},
    "scan.no_signal.impact": {
        "en": "{Next} on that channel records a blank screen",
        "es": "{Next} en ese canal se grabará con la pantalla en blanco",
    },
    "scan.no_signal.fix": {
        "en": "Check the camera or the HDMI/SDI cable feeding it",
        "es": "Revisa la cámara o el cable HDMI/SDI que la alimenta",
    },
    "scan.idle_input.what": {"en": "An input on {name} has no signal", "es": "Una entrada de {name} no tiene señal"},
    "scan.idle_input.impact": {
        "en": "Nothing, if that input isn't used by a channel",
        "es": "Nada, si ningún canal usa esa entrada",
    },
    "scan.idle_input.fix": {
        "en": "Unplug it or tidy the cable when you're next in the room",
        "es": "Desconéctala u ordena el cable cuando estés en la sala",
    },
    "scan.firmware.what": {
        "en": "{name} runs firmware {firmware}; others like it run {newest}",
        "es": "{name} usa el firmware {firmware}; otros equipos iguales usan {newest}",
    },
    "scan.firmware.impact": {
        "en": "Works fine today; keeps the fleet consistent",
        "es": "Funciona bien hoy; mantiene la flota uniforme",
    },
    "scan.firmware.fix": {
        "en": "Update firmware when the room is free",
        "es": "Actualiza el firmware cuando la sala esté libre",
    },
    "scan.cpu.what": {
        "en": "{name} is working hard (CPU at {pct}%)",
        "es": "{name} está trabajando al máximo (CPU al {pct}%)",
    },
    "scan.cpu.impact": {
        "en": "Streams may stutter if it stays this high",
        "es": "Las transmisiones pueden entrecortarse si se mantiene tan alto",
    },
    "scan.cpu.fix": {
        "en": "Check for an unused channel or a layout that's too heavy",
        "es": "Revisa si hay un canal sin usar o un diseño demasiado pesado",
    },
    "scan.temp.what": {
        "en": "{name} is running warm ({temp}°C)",
        "es": "{name} se está calentando más de lo normal ({temp} °C)",
    },
    "scan.temp.impact": {"en": "It may slow down to cool off", "es": "Puede reducir su velocidad para enfriarse"},
    "scan.temp.fix": {
        "en": "Check the vents and airflow around it",
        "es": "Revisa las rejillas y la ventilación a su alrededor",
    },
    "scan.reboot.what": {"en": "{name} restarted {ago}", "es": "{name} se reinició {ago}"},
    "ago.now": {"en": "just now", "es": "hace un momento"},
    "ago.one_minute": {"en": "about 1 minute ago", "es": "hace un minuto"},
    "ago.minutes": {"en": "about {mins} minutes ago", "es": "hace unos {mins} minutos"},
    "scan.reboot.impact": {
        "en": "Anything that was recording then stopped",
        "es": "Lo que se estaba grabando en ese momento se detuvo",
    },
    "scan.reboot.fix": {
        "en": "Check it came back with the right settings",
        "es": "Revisa que haya vuelto con la configuración correcta",
    },
    "scan.storage.one": {
        "en": "FYI: {n} Pearl has little or no local space left. That's normal when recordings upload to your CMS.",
        "es": "Para tu información: {n} Pearl tiene poco o nada de espacio local. "
        "Es normal cuando las grabaciones se suben a tu CMS.",
    },
    "scan.storage.many": {
        "en": "FYI: {n} Pearls have little or no local space left. That's normal when recordings upload to your CMS.",
        "es": "Para tu información: {n} Pearls tienen poco o nada de espacio local. "
        "Es normal cuando las grabaciones se suben a tu CMS.",
    },
    # --- readiness notes ---------------------------------------------------------------------------------------
    "ready.camera_offline": {
        "en": "The camera is offline, so its picture may be missing from the Pearl channels that use it, "
        "and Edge can't control it",
        "es": "La cámara está sin conexión, así que su imagen puede faltar en los canales Pearl que la usan, "
        "y Edge no puede controlarla",
    },
    "ready.unit_offline": {
        "en": "The unit is offline, so {the_event} won't record",
        "es": "La unidad está sin conexión, así que {the_event} no se grabará",
    },
    "ready.ec20": {
        "en": "This is an EC20 camera: it can't record or stream on its own",
        "es": "Esta es una cámara EC20: no puede grabar ni transmitir por sí sola",
    },
    "ready.no_picture": {"en": "No picture on {channels}", "es": "Sin imagen en {channels}"},
    "ready.already_recording": {
        "en": "Already recording; {the_scheduled} may start a second file",
        "es": "Ya está grabando; {the_scheduled} puede iniciar un segundo archivo",
    },
    "ready.idle_input": {
        "en": "One input has no signal (not used by a channel, as far as we can see)",
        "es": "Una entrada no tiene señal (ningún canal la usa, hasta donde vemos)",
    },
    "ready.low_space": {
        "en": "Little local space left; fine when recordings upload to your CMS",
        "es": "Queda poco espacio local; no hay problema si las grabaciones se suben a tu CMS",
    },
    # --- the digest and the before-event line ------------------------------------------------------------------
    "digest.needs_attention": {"en": "Needs attention", "es": "Requiere atención"},
    "digest.fleet_check": {"en": "Fleet check", "es": "Revisión de la flota"},
    "digest.still_open": {"en": "Still open", "es": "Sigue pendiente"},
    "digest.back_to_normal": {"en": "Back to normal", "es": "De vuelta a la normalidad"},
    "digest.all_clear": {
        "en": "All clear. Nothing needs attention right now.",
        "es": "Todo en orden. Nada requiere atención por ahora.",
    },
    # --- fleetwatch run: the fleet can't be read (#124) -------------------------------------------------------------
    "run.cant_read": {
        "en": "Fleetwatch can't read the fleet: the last {n} heartbeats couldn't make sense of what Epiphan sent. "
        "It keeps trying. Run fleetwatch status on its host for the reason.",
        "es": "Fleetwatch no puede leer la flota: las últimas {n} revisiones no entendieron lo que envió Epiphan. "
        "Sigue intentando. Ejecuta fleetwatch status en su equipo para ver el motivo.",
    },
    "run.can_read_again": {
        "en": "Fleetwatch can read the fleet again.",
        "es": "Fleetwatch ya puede leer la flota de nuevo.",
    },
    "readiness.at": {"en": "at {when}", "es": "a las {when}"},
    "readiness.at_one": {"en": "at {when}", "es": "a la {when}"},  # Spanish says "a la 1:05", not "a las 1:05"
    "readiness.was": {"en": "(was {was})", "es": "(antes: {was})"},
    # --- ask: answers to a question ----------------------------------------------------------------------------
    "ask.help": {
        "en": "I can answer: what needs attention, what's offline, the last digest, "
        "and whether a room is ready (for example: is Main Stage ready).",
        "es": "Puedo responder: qué necesita atención, qué está sin conexión, el último resumen "
        "y si una sala está lista (por ejemplo: ¿está lista la Sala 204?).",
    },
    "ask.no_room": {
        "en": "I couldn't find that room. Ask what's offline to see room names, or use the exact name.",
        "es": "No encontré esa sala. Pregunta qué está sin conexión para ver los nombres, o usa el nombre exacto.",
    },
    "ask.more_rooms": {
        "en": "{n} more rooms match. Use the full name to narrow it down.",
        "es": "{n} salas más coinciden. Usa el nombre completo para acotar.",
    },
    "ask.room.offline": {"en": "{name}: offline", "es": "{name}: sin conexión"},
    "ask.room.online": {"en": "{name}: online", "es": "{name}: en línea"},
    "ask.room.calm": {
        "en": ", nothing needs attention. {NoEvent} is coming up that I know of.",
        "es": ", nada requiere atención. {NoEvent} está programado, que yo sepa.",
    },
    "ask.room.last_check": {
        "en": "Last check: {title} {at}: {verdict}",
        "es": "Última revisión: {title} {at}: {verdict}",
    },
    "ask.note": {"en": "Note: {note}", "es": "Nota: {note}"},
    "ask.unseen": {
        "en": "I haven't seen the fleet yet. Run a heartbeat first.",
        "es": "Todavía no he visto la flota. Ejecuta un heartbeat primero.",
    },
    "ask.all_online": {"en": "Everything is online.", "es": "Todo está en línea."},
    "ask.offline_head": {"en": "{n} offline:", "es": "{n} sin conexión:"},
    "ask.nothing": {"en": "Nothing needs attention right now.", "es": "Nada requiere atención por ahora."},
    "ask.no_digest": {
        "en": "No digest has been posted yet.",
        "es": "Todavía no se ha publicado ningún resumen.",
    },
    "ask.digest_head": {"en": "Last digest {at}: {counts}.", "es": "Último resumen {at}: {counts}."},
    "ask.digest_new": {"en": "{n} new", "es": "{n} nuevos"},
    "ask.digest_reminders": {"en": "{n} reminders", "es": "{n} recordatorios"},
    "ask.digest_resolved": {"en": "{n} back to normal", "es": "{n} de vuelta a la normalidad"},
    "ask.checked_never": {"en": "Not checked yet.", "es": "Aún no se ha revisado."},
    "ask.checked_today": {"en": "Last checked {time}.", "es": "Última revisión: {time}."},
    "ask.checked_other": {"en": "Last checked {date} {time}.", "es": "Última revisión: {date} {time}."},
    # --- the nightly sweep and history -------------------------------------------------------------------------
    "sweep.head": {
        "en": "*Nightly sweep* · {devices} devices, {online} online",
        "es": "*Revisión nocturna* · {devices} equipos, {online} en línea",
    },
    "sweep.offline": {"en": "• Offline ({n}): {names}", "es": "• Sin conexión ({n}): {names}"},
    "sweep.behind": {
        "en": "• Behind on firmware ({n}): {shown}{more}",
        "es": "• Con firmware atrasado ({n}): {shown}{more}",
    },
    "sweep.behind_item": {
        "en": "{name} ({firmware}; newest {newest})",
        "es": "{name} ({firmware}; el más reciente {newest})",
    },
    "sweep.more": {"en": ", and {n} more", "es": ", y {n} más"},
    "sweep.newly_offline": {"en": "newly offline: {names}", "es": "ahora sin conexión: {names}"},
    "sweep.back_online": {"en": "back online: {names}", "es": "de vuelta en línea: {names}"},
    "sweep.changes": {"en": "• Since the last sweep, {changes}", "es": "• Desde la última revisión, {changes}"},
    "sweep.all_good": {
        "en": "Everything is online and on the same firmware.",
        "es": "Todo está en línea y con el mismo firmware.",
    },
    "history.none": {
        "en": "No history yet. It starts with the first heartbeat.",
        "es": "Todavía no hay historial. Empieza con el primer heartbeat.",
    },
    "history.head": {"en": "History since {since} (now {now})", "es": "Historial desde {since} (ahora {now})"},
    "history.fleet": {"en": "{size} devices, {span} online", "es": "{size} equipos, {span} en línea"},
    "history.digest.one": {"en": "1 digest", "es": "1 resumen"},
    "history.digest.many": {"en": "{n} digests", "es": "{n} resúmenes"},
    "history.readiness.one": {"en": "1 readiness check", "es": "1 revisión previa al evento"},
    "history.readiness.many": {"en": "{n} readiness checks", "es": "{n} revisiones previas al evento"},
    "history.sweep": {
        "en": "sweep: {offline} offline, {behind} behind on firmware",
        "es": "revisión nocturna: {offline} sin conexión, {behind} con firmware atrasado",
    },
    # --- the ask page (fleetwatch ask --serve). Each button's question goes to ask.answer() as written. -----------
    "page.title": {"en": "Ask Fleetwatch", "es": "Pregúntale a Fleetwatch"},
    "page.sub": {
        "en": "Read-only. Answers come from the last heartbeat; nothing here changes a device.",
        "es": "Solo lectura. Las respuestas vienen del último heartbeat; nada aquí cambia un equipo.",
    },
    "page.attention": {"en": "What needs attention?", "es": "¿Qué necesita atención?"},
    "page.offline": {"en": "What's offline?", "es": "¿Qué está sin conexión?"},
    "page.last_digest": {"en": "Last digest", "es": "Último resumen"},
    "page.room": {"en": "Is {room} ready?", "es": "¿Todo listo en {room}?"},
    "page.placeholder": {"en": "Is Main Stage ready?", "es": "¿Todo listo en la Sala 204?"},
    "page.your_question": {"en": "Your question", "es": "Tu pregunta"},
    "page.ask": {"en": "Ask", "es": "Preguntar"},
    "page.serving": {
        "en": "Ask Fleetwatch: {url}  (Ctrl+C to stop)",
        "es": "Pregúntale a Fleetwatch: {url}  (Ctrl+C para detener)",
    },
    # --- fleetwatch status and status --check ------------------------------------------------------------------
    "status.signed_in": {"en": "Signed in: yes", "es": "Sesión iniciada: sí"},
    "status.signed_out": {
        "en": "Signed in: no (run fleetwatch login)",
        "es": "Sesión iniciada: no (ejecuta fleetwatch login)",
    },
    "status.open_items": {"en": "Open items: {n}  ({date} UTC)", "es": "Pendientes: {n}  ({date} UTC)"},
    "status.off_schema": {
        "en": "Off-schema results from Epiphan: {tools} (read anyway; last 24 h)",
        "es": "Resultados de Epiphan fuera del esquema: {tools} (se leyeron de todos modos; últimas 24 h)",
    },
    "status.last_failed": {
        "en": "Last heartbeat failed at {when}: {reason}",
        "es": "El último heartbeat falló el {when}: {reason}",
    },
    "status.no_heartbeat": {
        "en": "No heartbeat yet (expected every {mins} min)",
        "es": "Todavía no hay ningún heartbeat (se espera uno cada {mins} min)",
    },
    "status.heartbeat_ok": {"en": "Last heartbeat {ago} min ago", "es": "Último heartbeat hace {ago} min"},
    "status.heartbeat_late": {
        "en": "Last heartbeat {ago} min ago; expected every {mins} min",
        "es": "Último heartbeat hace {ago} min; se espera uno cada {mins} min",
    },
    # --- fleetwatch doctor: row names, then details. OK/WARN/FAIL/INFO stay as they are (exit codes, tests). -----
    "doctor.name.version": {"en": "Version", "es": "Versión"},
    "doctor.name.policy": {"en": "Policy", "es": "Política"},
    "doctor.name.guard": {"en": "Read-only guard", "es": "Protección de solo lectura"},
    "doctor.name.redaction": {"en": "Redaction", "es": "Ocultamiento de secretos"},
    "doctor.name.sign_in": {"en": "Sign-in", "es": "Inicio de sesión"},
    "doctor.name.write_capable": {"en": "Sign-in can write", "es": "La sesión puede hacer cambios"},
    "doctor.name.issuer": {"en": "Sign-in issuer", "es": "Emisor del inicio de sesión"},
    "doctor.name.keychain": {"en": "Keychain access", "es": "Acceso al llavero"},
    "doctor.name.token_expiry": {"en": "Token expiry", "es": "Vencimiento del token"},
    "doctor.name.sandbox": {"en": "Sandbox sign-in", "es": "Sesión del sandbox"},
    "doctor.name.fence": {"en": "Write fence", "es": "Límite de cambios"},
    "doctor.name.state_dir": {"en": "State folder", "es": "Carpeta de estado"},
    "doctor.name.last_beat": {"en": "Last heartbeat", "es": "Último heartbeat"},
    "doctor.name.audit": {"en": "Audit log", "es": "Registro de auditoría"},
    "doctor.name.queue": {"en": "Proposal queue", "es": "Cola de propuestas"},
    "doctor.name.slack_commands": {"en": "Slack commands", "es": "Comandos de Slack"},
    "doctor.name.assistant": {"en": "Assistant", "es": "Asistente"},
    "doctor.name.epiphan_reach": {"en": "Epiphan reachable", "es": "Conexión con Epiphan"},
    "doctor.name.slack_reach": {"en": "Slack reachable", "es": "Conexión con Slack"},
    "doctor.name.service": {"en": "Service", "es": "Servicio"},
    "doctor.version": {
        "en": "fleetwatch {v}, Python {py} on {machine}",
        "es": "fleetwatch {v}, Python {py} en {machine}",
    },
    "doctor.version.unknown": {"en": "unknown", "es": "desconocida"},
    "doctor.policy.propose": {
        "en": "autonomy: propose: the assistant may propose changes for a person to approve; the heartbeat stays "
        "observe-only, and nothing runs without an approval",
        "es": "autonomy: propose: el asistente puede proponer cambios para que una persona los apruebe; el heartbeat "
        "solo observa y nada se ejecuta sin una aprobación",
    },
    "doctor.policy.observe": {"en": "observe-only", "es": "solo observa"},
    "doctor.policy.ok": {"en": "{mode}, heartbeat every {n} s", "es": "{mode}, heartbeat cada {n} s"},
    "doctor.guard.leaked": {
        "en": "write tools on the read list: {tools}",
        "es": "herramientas de escritura en la lista de lectura: {tools}",
    },
    "doctor.guard.ok": {
        "en": "{n} read tools allowed; every write tool is refused",
        "es": "{n} herramientas de lectura permitidas; se rechaza toda herramienta de escritura",
    },
    "doctor.redaction.fail": {
        "en": "a test stream key got through redact()",
        "es": "una clave de transmisión de prueba pasó por redact() sin ocultarse",
    },
    "doctor.redaction.ok": {
        "en": "stream keys and credentialed URLs are masked",
        "es": "las claves de transmisión y las URL con credenciales se ocultan",
    },
    "doctor.sign_in.static": {
        "en": "static token from FLEETWATCH_EPIPHAN_TOKEN",
        "es": "token fijo de FLEETWATCH_EPIPHAN_TOKEN",
    },
    "doctor.sign_in.none": {
        "en": "not signed in: run  fleetwatch login",
        "es": "no has iniciado sesión: ejecuta  fleetwatch login",
    },
    "doctor.sign_in.store": {"en": "token in {where}", "es": "token en {where}"},
    "doctor.sign_in.readable": {
        "en": "{f} is readable by other users (mode {mode}): run  chmod 600 {f}",
        "es": "otros usuarios pueden leer {f} (modo {mode}): ejecuta  chmod 600 {f}",
    },
    "doctor.sign_in.file": {"en": "token in {f} (mode 600)", "es": "token en {f} (modo 600)"},
    "doctor.write_capable": {
        "en": "the Edge sign-in can make changes if the account can; use a least-access account (docs/sign-in.md)",
        "es": "la sesión de Edge puede hacer cambios si la cuenta puede; usa una cuenta con el mínimo acceso "
        "(docs/sign-in.md)",
    },
    "doctor.write_capable.scope": {
        "en": "{advice}; granted scope: {scope}",
        "es": "{advice}; alcance otorgado: {scope}",
    },
    "doctor.issuer.none": {
        "en": "not saved yet; the next fleetwatch login saves it",
        "es": "aún no se guarda; el próximo fleetwatch login lo guarda",
    },
    "doctor.issuer.yes": {
        "en": "expected {issuer}; Epiphan says its redirect sends iss: yes",
        "es": "se espera {issuer}; Epiphan dice que su redirección envía iss: sí",
    },
    "doctor.issuer.no": {
        "en": "expected {issuer}; Epiphan says its redirect sends iss: no",
        "es": "se espera {issuer}; Epiphan dice que su redirección envía iss: no",
    },
    "doctor.issuer.unstated": {
        "en": "expected {issuer}; Epiphan says its redirect sends iss: not stated",
        "es": "se espera {issuer}; Epiphan no dice si su redirección envía iss",
    },
    "doctor.keychain": {
        "en": "any program running as the same macOS user can read the token; give Fleetwatch its own user "
        "(docs/mac-mini.md)",
        "es": "cualquier programa que se ejecute con el mismo usuario de macOS puede leer el token; dale a "
        "Fleetwatch su propio usuario (docs/mac-mini.md)",
    },
    "doctor.expiry.dead": {
        "en": "Epiphan refused the refresh token: sign in again with  fleetwatch login",
        "es": "Epiphan rechazó el token de renovación: vuelve a iniciar sesión con  fleetwatch login",
    },
    "doctor.expiry.unknown": {
        "en": "not recorded yet; saved with the next refresh",
        "es": "aún no se registra; se guarda con la próxima renovación",
    },
    "doctor.expiry.expired": {
        "en": "expired {when}; the next heartbeat will refresh it",
        "es": "venció el {when}; el próximo heartbeat lo renovará",
    },
    "doctor.expiry.ok": {
        "en": "{when} (refreshes itself before then)",
        "es": "{when} (se renueva solo antes de esa hora)",
    },
    "doctor.sandbox.none": {
        "en": "none, so no change can run",
        "es": "ninguna, así que ningún cambio puede ejecutarse",
    },
    "doctor.sandbox.readable": {
        "en": "{f} is readable by other users: run  chmod 600 {f}",
        "es": "otros usuarios pueden leer {f}: ejecuta  chmod 600 {f}",
    },
    "doctor.sandbox.ok": {
        "en": "signed in, token in {where}; only changes a person approves (v0.2) use it",
        "es": "sesión iniciada, token en {where}; solo la usan los cambios que una persona aprueba (v0.2)",
    },
    "doctor.fence.no_policy": {
        "en": "can't tell until policy.yaml loads",
        "es": "no se puede saber hasta que cargue policy.yaml",
    },
    "doctor.fence.observe": {
        "en": "not needed: autonomy is observe, so no change can be proposed",
        "es": "no hace falta: autonomy es observe, así que no se puede proponer ningún cambio",
    },
    "doctor.fence.invalid.one": {
        "en": "{n} entry in FLEETWATCH_WRITE_DEVICE_IDS isn't a device ID ({shown}). List master device IDs only, "
        "comma-separated: 8 to 32 characters, 0-9 and a-f. A channel ID such as 0a1b2c3d-1 doesn't go here.",
        "es": "{n} entrada de FLEETWATCH_WRITE_DEVICE_IDS no es un ID de equipo ({shown}). Escribe solo IDs de "
        "equipo principales, separados por comas: de 8 a 32 caracteres, 0-9 y a-f. Un ID de canal como "
        "0a1b2c3d-1 no va aquí.",
    },
    "doctor.fence.invalid.many": {
        "en": "{n} entries in FLEETWATCH_WRITE_DEVICE_IDS aren't a device ID ({shown}). List master device IDs only, "
        "comma-separated: 8 to 32 characters, 0-9 and a-f. A channel ID such as 0a1b2c3d-1 doesn't go here.",
        "es": "{n} entradas de FLEETWATCH_WRITE_DEVICE_IDS no son IDs de equipo ({shown}). Escribe solo IDs de "
        "equipo principales, separados por comas: de 8 a 32 caracteres, 0-9 y a-f. Un ID de canal como "
        "0a1b2c3d-1 no va aquí.",
    },
    "doctor.fence.unset": {
        "en": "autonomy: propose needs a fence, or every change is refused: list the sandbox devices in "
        "FLEETWATCH_WRITE_DEVICE_IDS (or set FLEETWATCH_WRITE_TEAM_ID)",
        "es": "autonomy: propose necesita un límite, o se rechaza todo cambio: escribe los equipos del sandbox en "
        "FLEETWATCH_WRITE_DEVICE_IDS (o configura FLEETWATCH_WRITE_TEAM_ID)",
    },
    "doctor.fence.team_only": {
        "en": "only a team ID is set. Epiphan may not report one, and then every change is refused: also list the "
        "sandbox devices in FLEETWATCH_WRITE_DEVICE_IDS",
        "es": "solo hay un ID de team. Puede que Epiphan no informe ninguno, y entonces se rechaza todo cambio: "
        "escribe también los equipos del sandbox en FLEETWATCH_WRITE_DEVICE_IDS",
    },
    "doctor.fence.ok.one": {
        "en": "{n} sandbox device on the allowlist",
        "es": "{n} equipo del sandbox en la lista permitida",
    },
    "doctor.fence.ok.many": {
        "en": "{n} sandbox devices on the allowlist",
        "es": "{n} equipos del sandbox en la lista permitida",
    },
    "doctor.fence.team_too": {"en": "; a team ID is also set", "es": "; también hay un ID de team"},
    "doctor.state_dir.unwritable": {"en": "{d} is not writable", "es": "no se puede escribir en {d}"},
    "doctor.state_dir.new": {"en": "{d} (created on first run)", "es": "{d} (se crea en la primera ejecución)"},
    # {reason} is stored as it was recorded (redacted, in English): it passes through untranslated.
    "doctor.last_beat": {"en": "failed at {when}: {reason}", "es": "falló el {when}: {reason}"},
    "doctor.audit.none": {"en": "no history yet", "es": "todavía no hay historial"},
    "doctor.audit.unreadable": {
        "en": "couldn't read the audit log ({error})",
        "es": "no se pudo leer el registro de auditoría ({error})",
    },
    "doctor.audit.ok": {
        "en": "no row has been changed or removed",
        "es": "ninguna fila se ha cambiado ni eliminado",
    },
    "doctor.audit.bad": {
        "en": "row {row} doesn't match the one before it: the log was edited or damaged",
        "es": "la fila {row} no coincide con la anterior: el registro se editó o se dañó",
    },
    "doctor.queue.unreadable": {
        "en": "couldn't read the proposal queue ({error})",
        "es": "no se pudo leer la cola de propuestas ({error})",
    },
    "doctor.queue.counts": {
        "en": "{pending} of {max_pending} waiting, {hour} of {max_hour} in the last hour",
        "es": "{pending} de {max_pending} en espera, {hour} de {max_hour} en la última hora",
    },
    "doctor.queue.full_pending": {
        "en": "full: {counts}. The assistant can't propose more until you approve or deny one",
        "es": "llena: {counts}. El asistente no puede proponer más hasta que apruebes o rechaces una",
    },
    "doctor.queue.full_hour": {
        "en": "full: {counts}. The assistant can't propose more until the hour passes",
        "es": "llena: {counts}. El asistente no puede proponer más hasta que pase la hora",
    },
    "doctor.reach.fail": {
        "en": "can't reach {host}: check the network, DNS or a proxy",
        "es": "no se puede conectar con {host}: revisa la red, el DNS o el proxy",
    },
    "doctor.assistant.off": {"en": "off (no API key)", "es": "apagado (sin clave de API)"},
    "doctor.assistant.on": {
        "en": "on, model {model}, sends redacted fleet data to the Anthropic API (key {mask}; "
        "leave FLEETWATCH_ANTHROPIC_API_KEY empty to turn it off)",
        "es": "encendido, modelo {model}, envía datos de la flota con los secretos ocultos a la API de Anthropic "
        "(clave {mask}; deja FLEETWATCH_ANTHROPIC_API_KEY vacía para apagarlo)",
    },
    "doctor.assistant.key_set": {"en": "set", "es": "configurada"},
    "doctor.slack.posts": {"en": "posts to {channel}", "es": "publica en {channel}"},
    "doctor.slack.console": {
        "en": "no token: prints to the console",
        "es": "sin token: lo muestra en la consola",
    },
    "doctor.teams.none": {"en": "not configured", "es": "sin configurar"},
    "doctor.teams.https": {
        "en": "FLEETWATCH_TEAMS_WEBHOOK_URL should start with https://",
        "es": "FLEETWATCH_TEAMS_WEBHOOK_URL debe empezar con https://",
    },
    "doctor.teams.ok": {"en": "configured", "es": "configurado"},
    "doctor.slack_commands.off": {
        "en": "off (set FLEETWATCH_SLACK_APP_TOKEN to answer /fleetwatch)",
        "es": "apagado (configura FLEETWATCH_SLACK_APP_TOKEN para responder a /fleetwatch)",
    },
    "doctor.slack_commands.xapp": {
        "en": "FLEETWATCH_SLACK_APP_TOKEN must be an app-level token starting xapp-",
        "es": "FLEETWATCH_SLACK_APP_TOKEN debe ser un token de nivel de app que empiece con xapp-",
    },
    "doctor.slack_commands.no_policy": {
        "en": "can't read the allowlist until policy.yaml loads",
        "es": "no se puede leer la lista permitida hasta que cargue policy.yaml",
    },
    "doctor.slack_commands.group_needs_bot": {
        "en": "slack.allowed_usergroup needs FLEETWATCH_SLACK_BOT_TOKEN (usergroups:read)",
        "es": "slack.allowed_usergroup necesita FLEETWATCH_SLACK_BOT_TOKEN (usergroups:read)",
    },
    "doctor.slack_commands.nobody": {
        "en": "nobody is allowed yet: add slack.allowed_user_ids in policy.yaml",
        "es": "todavía no hay nadie permitido: agrega slack.allowed_user_ids en policy.yaml",
    },
    "doctor.slack_commands.one": {"en": "on, answers {n} user", "es": "encendido, responde a {n} usuario"},
    "doctor.slack_commands.many": {"en": "on, answers {n} users", "es": "encendido, responde a {n} usuarios"},
    "doctor.slack_commands.one_group": {
        "en": "on, answers {n} user and a user group",
        "es": "encendido, responde a {n} usuario y a un grupo de usuarios",
    },
    "doctor.slack_commands.many_group": {
        "en": "on, answers {n} users and a user group",
        "es": "encendido, responde a {n} usuarios y a un grupo de usuarios",
    },
    "doctor.service.launchd_missing": {
        "en": "launchd agent not installed: run  deploy/install.sh",
        "es": "el agente de launchd no está instalado: ejecuta  deploy/install.sh",
    },
    "doctor.service.launchd_running": {"en": "launchd agent running", "es": "el agente de launchd está funcionando"},
    "doctor.service.launchd_stopped": {
        "en": "launchd agent loaded, not running",
        "es": "el agente de launchd está cargado, pero no está funcionando",
    },
    "doctor.service.systemd_active": {
        "en": "systemd user unit active",
        "es": "la unidad de usuario de systemd está activa",
    },
    # {state} is systemctl's own word (inactive, failed, ...), shown as it printed it.
    "doctor.service.systemd_other": {
        "en": "systemd user unit {state}: run  deploy/install.sh  (or check  journalctl --user -u fleetwatch)",
        "es": "unidad de usuario de systemd en estado {state}: ejecuta  deploy/install.sh  "
        "(o revisa  journalctl --user -u fleetwatch)",
    },
    "doctor.service.container": {"en": "running in a container", "es": "funcionando en un contenedor"},
    "doctor.service.none": {
        "en": "no launchd or systemd here ({system}); run  fleetwatch run  yourself",
        "es": "aquí no hay launchd ni systemd ({system}); ejecuta  fleetwatch run  tú mismo",
    },
    "doctor.service.dead": {
        "en": "stopped: sign-in expired. Run  fleetwatch login, then  {restart}",
        "es": "detenido: la sesión venció. Ejecuta  fleetwatch login y luego  {restart}",
    },
    "doctor.summary.fix": {
        "en": "{fails} to fix before relying on Fleetwatch.",
        "es": "{fails} por arreglar antes de confiar en Fleetwatch.",
    },
    "doctor.summary.fix_look": {
        "en": "{fails} to fix before relying on Fleetwatch, {warns} to look at.",
        "es": "{fails} por arreglar antes de confiar en Fleetwatch, {warns} por revisar.",
    },
    "doctor.summary.look": {"en": "Nothing broken. {warns} to look at.", "es": "Nada está roto. {warns} por revisar."},
    "doctor.summary.ok": {"en": "All good.", "es": "Todo bien."},
    # --- room notes --------------------------------------------------------------------------------------------
    "notes.empty": {"en": "That note is empty. Nothing saved.", "es": "Esa nota está vacía. No se guardó nada."},
    "notes.no_room": {
        "en": "I couldn't find that room. Run fleetwatch notes to see rooms with notes, or use the exact name.",
        "es": "No encontré esa sala. Ejecuta fleetwatch notes para ver las salas con notas, o usa el nombre exacto.",
    },
    "notes.many_rooms": {
        "en": "More than one room matches. Use the full name:",
        "es": "Coincide más de una sala. Usa el nombre completo:",
    },
    "notes.and_more": {"en": "...and {n} more", "es": "...y {n} más"},
    "notes.saved": {"en": "Saved a note on {name}.", "es": "Nota guardada en {name}."},
    "notes.no_room_short": {"en": "I couldn't find that room.", "es": "No encontré esa sala."},
    "notes.no_match": {"en": "No notes match.", "es": "Ninguna nota coincide."},
    "notes.none": {"en": "No notes yet.", "es": "Todavía no hay notas."},
}


_SPANISH = re.compile(
    r"[¿¡ñáéíóú]|\b(qué|que|cuál|cual|cuáles|cómo|como|está|esta|están|hay|necesita|sala|salas|listo|lista|"
    r"desconectad[oa]s?|atención|problemas?|grabando|grabación|evento|clase|ahora|por|favor|dónde|donde)\b",
    re.IGNORECASE,
)

_MONTHS_ES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def detect(question: str | None) -> str:
    """`es` when a question reads as Spanish, else `en`."""
    return "es" if _SPANISH.search(question or "") else "en"


def pick(question: str | None, policy: Any) -> str:
    """The language to answer a question in: Spanish if the policy says so or the question is Spanish."""
    return "es" if getattr(policy, "language", "en") == "es" or detect(question) == "es" else "en"


def short_date(at: Any, lang: str) -> str:
    """Oct 7 (English) or 7 oct (Spanish), in local time."""
    local = at.astimezone()
    if _lang(lang) == "es":
        return f"{local.day} {_MONTHS_ES[local.month - 1]}"
    return f"{local:%b} {local.day}"


def at_clock(lang: str, at: Any) -> str:
    """`at 6:35 PM` or `a las 6:35 PM` (`a la 1:05 PM`), in local time."""
    local = at.astimezone()
    key = "readiness.at_one" if local.hour % 12 == 1 else "readiness.at"
    return t(lang, key, when=local.strftime("%-I:%M %p"))


def _lang(lang: str) -> str:
    return lang if lang in LANGUAGES else "en"


def vocab(lang: str, vertical: str) -> dict[str, str]:
    table = _VOCAB[_lang(lang)]
    return table.get(vertical) or table["events"]


def t(lang: str, key: str, **kw: Any) -> str:
    return MESSAGES[key][_lang(lang)].format(**kw)


def minutes_ago(lang: str, mins: int) -> str:
    """ "just now", "about 1 minute ago", "about 5 minutes ago" (#126: the soak showed "1 minutes")."""
    if mins <= 0:
        return t(lang, "ago.now")
    if mins == 1:
        return t(lang, "ago.one_minute")
    return t(lang, "ago.minutes", mins=mins)


def say(policy: Any, key: str, **kw: Any) -> str:
    """`t()` for a policy: its `language`, plus the words for its `vertical`."""
    return t(policy.language, key, **{**vocab(policy.language, policy.vertical), **kw})


def priority_label(value: str, lang: str) -> str:
    return PRIORITY[_lang(lang)].get(value, value)


def verdict_label(verdict: str, lang: str) -> str:
    return VERDICT[_lang(lang)].get(verdict, verdict)


def verdict_now_label(verdict: str, lang: str) -> str:
    return VERDICT_NOW[_lang(lang)].get(verdict, verdict)
