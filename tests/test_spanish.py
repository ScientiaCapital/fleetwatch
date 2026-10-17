"""One setting, `language: es`, puts what people read in Mexican Spanish. Stored values stay English."""

import string
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from fleetwatch.agents.readiness.rules import NOT_READY, NOTES, READY, check
from fleetwatch.agents.scanner.rules import scan
from fleetwatch.i18n import _VOCAB, LANGUAGES, MESSAGES, PRIORITY, VERDICT, VERDICT_NOW, say, t
from fleetwatch.model import Channel, Device, Event, Finding, Fleet, Priority, Readiness
from fleetwatch.notify.digest import render_digest, render_readiness
from fleetwatch.policy import VERTICALS, Policy, load_policy
from tests.conftest import NOW

ES = Policy(language="es")


def fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def test_every_message_says_the_same_things_in_both_languages():
    for key, by_lang in MESSAGES.items():
        assert set(by_lang) == set(LANGUAGES), key
        assert fields(by_lang["en"]) == fields(by_lang["es"]), f"{key}: the placeholders differ"
        assert by_lang["es"] != by_lang["en"], f"{key}: not translated"


def test_every_vertical_has_its_spanish_words():
    assert set(_VOCAB["es"]) == set(_VOCAB["en"]) == set(VERTICALS)
    for vertical, words in _VOCAB["es"].items():
        assert set(words) == set(_VOCAB["en"][vertical])


def test_english_words_match_the_policy_verticals():
    for vertical, (one, many) in VERTICALS.items():
        assert _VOCAB["en"][vertical]["event"] == one
        assert _VOCAB["en"][vertical]["Events"] == many.capitalize()


def test_priorities_and_verdicts_are_translated_but_the_stored_values_are_not():
    assert Priority.FIX_FIRST.value == "Fix first"
    assert [p.label("es") for p in Priority] == ["Arreglar primero", "Arreglar pronto", "Cuando convenga"]
    assert [p.label("en") for p in Priority] == [p.value for p in Priority]
    assert set(PRIORITY["es"]) == set(PRIORITY["en"]) and set(VERDICT["es"]) == set(VERDICT_NOW["es"])


def test_language_loads_from_policy_yaml(tmp_path):
    p = tmp_path / "policy.yaml"
    assert load_policy(p).language == "en"
    p.write_text("language: es\n")
    assert load_policy(p).language == "es"
    p.write_text("language: fr\n")
    with pytest.raises(ValueError, match="language must be one of en, es"):
        load_policy(p)


def test_a_fleet_scans_to_the_same_findings_in_either_language(fleet, policy):
    en, es = scan(fleet, policy), scan(fleet, replace(policy, language="es"))
    assert [(f.key, f.priority, f.device_id, f.fyi) for f in en] == [
        (f.key, f.priority, f.device_id, f.fyi) for f in es
    ]
    assert len(en) > 5, "the sample fleet has problems to translate"
    for f in es:
        assert f.what and "is offline" not in f.what and "FYI" not in f.what
        assert not f.impact.startswith(("Edge can't", "Streams may", "Works fine"))


def test_the_spanish_digest_has_no_english_labels(fleet, policy):
    es = replace(policy, language="es")
    text = render_digest(scan(fleet, es), [], [], lang="es")
    assert text and "*Requiere atención*" in text and "*Arreglar primero*" in text
    for english in ("Needs attention", "Fix first", "Fix soon", "When convenient", "is offline", "FYI"):
        assert english not in text, english


def test_the_other_digest_headings_in_spanish():
    done = Finding(
        key="d:offline", priority=Priority.FIX_FIRST, device_id="d", device_name="Sala 1", what="Sala 1 volvió"
    )
    text = render_digest([], [done], [done], lang="es")
    assert "*Sigue pendiente*" in text and "*De vuelta a la normalidad*" in text
    assert render_digest([], [], [], first_run=True, lang="es") == "Todo en orden. Nada requiere atención por ahora."
    assert "*Revisión de la flota*" in render_digest([done], [], [], first_run=True, lang="es")


def test_the_words_for_a_scheduled_recording_agree_in_gender_and_number():
    fleet = Fleet(taken_at=NOW, devices={"d": Device(id="d", name="Salón 3", model="Pearl-2", online=False)})
    impacts = {
        v: scan(fleet, replace(ES, vertical=v))[0].impact
        for v in ("events", "education", "business", "courts", "worship")
    }
    assert impacts["events"].startswith("Los eventos en esa sala")
    assert impacts["education"].startswith("Las clases en esa sala")
    assert impacts["business"].startswith("Las reuniones en esa sala")
    assert impacts["courts"].startswith("Las audiencias en esa sala")
    assert impacts["worship"].startswith("Los servicios en esa sala")
    assert say(replace(ES, vertical="education"), "ready.already_recording") == (
        "Ya está grabando; la clase programada puede iniciar un segundo archivo"
    )
    assert t("es", "scan.no_signal.impact", **_VOCAB["es"]["worship"]).startswith("El próximo servicio")


def _pearl(**kw) -> Device:
    return Device(
        id="1f000000001", name="Sala 204", model="Pearl Mini", channels={"1": Channel(id="1", name="Principal")}, **kw
    )


def test_readiness_verdicts_stay_english_and_the_notes_follow_the_language():
    event = Event(device_id="1f000000001", title="Clase 101", start=NOW + timedelta(minutes=20))
    blocked = check(_pearl(online=False), event, ES)
    assert blocked.verdict == NOT_READY and blocked.notes == (
        "La unidad está sin conexión, así que el evento no se grabará",
    )
    low_space = check(_pearl(warnings=["disk_space_error"]), event, ES)
    assert low_space.verdict == NOTES and low_space.notes[0].startswith("Queda poco espacio local")
    assert check(_pearl(), event, ES).verdict == READY


@pytest.fixture
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _verdict(verdict: str, hour: int = 18) -> Readiness:
    start = datetime(2026, 10, 7, hour, 35, tzinfo=UTC)
    return Readiness(event=Event(device_id="d", title="LAW 210", start=start), device_name="Sala 1", verdict=verdict)


def test_the_before_event_line_in_spanish(utc):
    assert render_readiness(_verdict(NOT_READY), lang="es") == "*Sala 1* · LAW 210 a las 6:35 PM: *No está listo*"
    assert render_readiness(_verdict(READY), NOT_READY, "es") == (
        "*Sala 1* · LAW 210 a las 6:35 PM: *Ya está listo* (antes: No está listo)"
    )
    assert render_readiness(_verdict(NOTES), lang="es").endswith("*Listo, con notas*")
    assert render_readiness(_verdict(READY, hour=13), lang="es").startswith("*Sala 1* · LAW 210 a la 1:35 PM")


def test_the_before_event_line_in_english_is_unchanged(utc):
    assert render_readiness(_verdict(NOT_READY)) == "*Sala 1* · LAW 210 at 6:35 PM: *Not ready*"
    assert render_readiness(_verdict(READY), NOT_READY) == "*Sala 1* · LAW 210 at 6:35 PM: *Ready now* (was Not ready)"
