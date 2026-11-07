"""`fleetwatch ask --serve`: the local page escapes what it shows and only talks to localhost."""

import http.client
import socket
import threading
from http.server import HTTPServer
from urllib.parse import urlencode

import pytest

from fleetwatch.ask_page import MAX_BODY, make_handler, render


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server():
    port = _free_port()
    asked = []

    def ask(q: str) -> str:
        asked.append(q)
        return "Main Stage · Keynote at 9:00 AM: Ready <b>"

    httpd = HTTPServer(("127.0.0.1", port), make_handler(ask, lambda: ['Ballroom "B" <img>'], port))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield port, asked
    httpd.shutdown()
    httpd.server_close()


def _req(port, method, path, body=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Host": host or f"127.0.0.1:{port}"}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    c.request(method, path, body=body, headers=headers)
    r = c.getresponse()
    return r.status, r.read().decode(), dict(r.getheaders())


def test_page_lists_buttons_and_escapes_room_names(server):
    port, _ = server
    status, body, headers = _req(port, "GET", "/")
    assert status == 200 and "What needs attention?" in body
    assert "Ballroom &quot;B&quot; &lt;img&gt;" in body and "<img>" not in body
    assert "default-src 'none'" in headers["Content-Security-Policy"]


def test_post_answers_and_escapes(server):
    port, asked = server
    status, body, _ = _req(port, "POST", "/ask", urlencode({"q": "is Main Stage ready <script>"}))
    assert status == 200 and asked == ["is Main Stage ready <script>"]
    assert "Ready &lt;b&gt;" in body and "&lt;script&gt;" in body and "<script>" not in body


def test_other_hosts_are_refused(server):
    port, asked = server
    assert _req(port, "GET", "/", host="evil.example:80")[0] == 403
    assert _req(port, "POST", "/ask", urlencode({"q": "x"}), host=f"attacker.test:{port}")[0] == 403
    assert asked == []


def test_large_body_and_unknown_paths(server):
    port, asked = server
    assert _req(port, "POST", "/ask", "q=" + "a" * MAX_BODY)[0] == 413
    assert _req(port, "GET", "/etc/passwd")[0] == 404
    assert asked == []


def test_render_without_answer_has_no_answer_block():
    assert 'class="answer"' not in render()


FOOTER = "Fleetwatch for Epiphan Edge · not an officially supported Epiphan product"


def test_render_in_english_by_default():
    page = render(rooms=["Room 204"])
    assert '<html lang="en">' in page and "<h1>Ask Fleetwatch</h1>" in page
    assert '<button name="q" value="What&#x27;s offline?">What&#x27;s offline?</button>' in page
    assert '<button name="q" value="Is Room 204 ready?">Is Room 204 ready?</button>' in page
    assert 'aria-label="Your question"' in page and ">Ask</button>" in page and FOOTER in page


def test_render_in_spanish_keeps_the_licensing_footer_in_english():
    page = render("Todo en orden.", "¿Qué necesita atención?", ['Sala "B" <img>'], "Última revisión: 09:00.", "es")
    assert '<html lang="es">' in page and "<title>Pregúntale a Fleetwatch</title>" in page
    assert "Solo lectura. Las respuestas vienen del último heartbeat" in page
    for label in ("¿Qué necesita atención?", "¿Qué está sin conexión?", "Último resumen"):
        assert f'<button name="q" value="{label}">{label}</button>' in page
    assert "¿Todo listo en Sala &quot;B&quot; &lt;img&gt;?" in page and "<img>" not in page
    assert 'placeholder="¿Todo listo en la Sala 204?" aria-label="Tu pregunta"' in page
    assert ">Preguntar</button>" in page and FOOTER in page
    for english in ("Ask Fleetwatch", "Read-only", "What needs attention", "offline?", "Last digest", "Your question"):
        assert english not in page, english


def test_an_unknown_language_falls_back_to_english():
    assert '<html lang="en">' in render(lang="fr") and "<h1>Ask Fleetwatch</h1>" in render(lang="fr")


def test_the_spanish_server_serves_the_spanish_page():
    port = _free_port()
    httpd = HTTPServer(
        ("127.0.0.1", port), make_handler(lambda q: "Todo en orden.", lambda: ["Sala 1"], port, lang="es")
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        status, body, _ = _req(port, "GET", "/")
        assert status == 200 and "¿Todo listo en Sala 1?" in body and "<h1>Pregúntale a Fleetwatch</h1>" in body
        status, body, _ = _req(port, "POST", "/ask", urlencode({"q": "¿Qué está sin conexión?"}))
        assert status == 200 and "Todo en orden." in body and ">Preguntar</button>" in body
    finally:
        httpd.shutdown()
        httpd.server_close()
