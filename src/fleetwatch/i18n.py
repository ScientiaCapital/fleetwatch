"""English and Spanish for what people read: findings, the digest and the before-event lines.

Stored values stay English whatever the language: priorities (`Priority.value`), verdicts (`READY` and the rest) and
finding keys. Only the text shown changes, so switching `language` in policy.yaml never reposts or reopens anything.
Spanish is Mexican Spanish, informal (tú), like README.es.md.

`t()` fills one message. `say()` also fills the words for a scheduled recording ({event}, {Events}, ...) from the
policy's `vertical`, because Spanish needs the article and the gender that go with each one.
"""

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
        },
        "education": {
            "event": "clase",
            "Events": "Las clases",
            "the_event": "la clase",
            "the_scheduled": "la clase programada",
            "Next": "La próxima clase",
        },
        "business": {
            "event": "reunión",
            "Events": "Las reuniones",
            "the_event": "la reunión",
            "the_scheduled": "la reunión programada",
            "Next": "La próxima reunión",
        },
        "courts": {
            "event": "audiencia",
            "Events": "Las audiencias",
            "the_event": "la audiencia",
            "the_scheduled": "la audiencia programada",
            "Next": "La próxima audiencia",
        },
        "worship": {
            "event": "servicio",
            "Events": "Los servicios",
            "the_event": "el servicio",
            "the_scheduled": "el servicio programado",
            "Next": "El próximo servicio",
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
    "scan.reboot.what": {
        "en": "{name} restarted about {mins} minutes ago",
        "es": "{name} se reinició hace unos {mins} minutos",
    },
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
    "readiness.at": {"en": "at {when}", "es": "a las {when}"},
    "readiness.at_one": {"en": "at {when}", "es": "a la {when}"},  # Spanish says "a la 1:05", not "a las 1:05"
    "readiness.was": {"en": "(was {was})", "es": "(antes: {was})"},
}


def _lang(lang: str) -> str:
    return lang if lang in LANGUAGES else "en"


def vocab(lang: str, vertical: str) -> dict[str, str]:
    table = _VOCAB[_lang(lang)]
    return table.get(vertical) or table["events"]


def t(lang: str, key: str, **kw: Any) -> str:
    return MESSAGES[key][_lang(lang)].format(**kw)


def say(policy: Any, key: str, **kw: Any) -> str:
    """`t()` for a policy: its `language`, plus the words for its `vertical`."""
    return t(policy.language, key, **{**vocab(policy.language, policy.vertical), **kw})


def priority_label(value: str, lang: str) -> str:
    return PRIORITY[_lang(lang)].get(value, value)


def verdict_label(verdict: str, lang: str) -> str:
    return VERDICT[_lang(lang)].get(verdict, verdict)


def verdict_now_label(verdict: str, lang: str) -> str:
    return VERDICT_NOW[_lang(lang)].get(verdict, verdict)
