"""`fleetwatch ask --serve`: one local page with buttons and a text box, for a booth screen or a tech's laptop.

Standard library only. Listens on 127.0.0.1, answers only requests addressed to localhost (so another web page
can't read answers through DNS rebinding), never calls Epiphan, and escapes everything it shows.
"""

import html
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

from fleetwatch.ask import MAX_QUESTION

MAX_BODY = 4096

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ask Fleetwatch</title>
<style>
:root {{ --bg:#f6f8fa; --card:#fff; --fg:#1f2328; --muted:#57606a; --line:#d0d7de; --accent:#1f6feb; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#0d1117; --card:#161b22; --fg:#f0f6fc; --muted:#9198a1; --line:#30363d; --accent:#4493f8; }}
}}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg);
  font: 18px/1.5 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; }}
main {{ max-width: 860px; margin: 0 auto; padding: 24px 16px 48px; }}
h1 {{ font-size: 28px; margin: 0 0 4px; }}
p.sub {{ color: var(--muted); margin: 0 0 20px; }}
form.row {{ display:flex; flex-wrap:wrap; gap:10px; margin: 0 0 12px; }}
button {{ font: inherit; padding: 12px 16px; border-radius: 10px; border: 1px solid var(--line);
  background: var(--card); color: var(--fg); cursor: pointer; min-height: 48px; }}
button.primary {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
input[type=text] {{ font: inherit; flex: 1 1 280px; padding: 12px 14px; border-radius: 10px;
  border: 1px solid var(--line); background: var(--card); color: var(--fg); min-height: 48px; }}
section.answer {{ margin-top: 20px; background: var(--card); border: 1px solid var(--line); border-radius: 12px;
  padding: 16px 18px; }}
section.answer h2 {{ font-size: 15px; color: var(--muted); margin: 0 0 8px; font-weight: 600; }}
pre {{ margin:0; white-space: pre-wrap; word-wrap: break-word;
  font: 17px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace; }}
footer {{ color: var(--muted); font-size: 14px; margin-top: 28px; }}
</style></head>
<body><main>
<h1>Ask Fleetwatch</h1>
<p class="sub">Read-only. Answers come from the last heartbeat; nothing here changes a device. {checked}</p>
<form class="row" method="post" action="/ask">
  <button name="q" value="What needs attention?">What needs attention?</button>
  <button name="q" value="What's offline?">What's offline?</button>
  <button name="q" value="Last digest">Last digest</button>
</form>
{rooms}
<form class="row" method="post" action="/ask">
  <input type="text" name="q" maxlength="{max_q}" placeholder="Is Main Stage ready?" aria-label="Your question"
    autocomplete="off" autofocus>
  <button class="primary" type="submit">Ask</button>
</form>
{answer}
<footer>Fleetwatch for Epiphan Edge · not an officially supported Epiphan product</footer>
</main></body></html>
"""


def render(
    answer: str | None = None, question: str | None = None, rooms: list[str] | None = None, checked: str = ""
) -> str:
    room_buttons = ""
    if rooms:
        buttons = "".join(
            f'<button name="q" value="Is {html.escape(r, quote=True)} ready?">Is {html.escape(r)} ready?</button>'
            for r in rooms
        )
        room_buttons = f'<form class="row" method="post" action="/ask">{buttons}</form>'
    block = ""
    if answer is not None:
        block = (
            '<section class="answer" aria-live="polite">'
            f"<h2>{html.escape(question or '')}</h2><pre>{html.escape(answer)}</pre></section>"
        )
    return _PAGE.format(rooms=room_buttons, answer=block, max_q=MAX_QUESTION, checked=html.escape(checked))


def make_handler(
    ask: Callable[[str], str], rooms: Callable[[], list[str]], port: int, checked: Callable[[], str] = lambda: ""
):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "fleetwatch"
        sys_version = ""

        def log_message(self, *_):  # keep the console for the digest
            pass

        def _send(self, status: int, body: str, content_type: str = "text/html; charset=utf-8") -> None:
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"
            )
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self) -> bool:
            if self.headers.get("Host", "") in allowed_hosts:
                return True
            self._send(403, "Forbidden", "text/plain; charset=utf-8")
            return False

        def do_GET(self):
            if not self._host_ok():
                return
            if self.path != "/":
                self._send(404, "Not found", "text/plain; charset=utf-8")
                return
            self._send(200, render(rooms=rooms(), checked=checked()))

        def do_POST(self):
            if not self._host_ok():
                return
            if self.path != "/ask":
                self._send(404, "Not found", "text/plain; charset=utf-8")
                return
            raw = (self.headers.get("Content-Length") or "0").strip()
            if not (
                raw.isascii() and raw.isdigit()
            ):  # negative, fractional or not a number: rfile.read(-1) would wait for EOF
                self._send(400, "Bad request", "text/plain; charset=utf-8")
                return
            length = int(raw)
            if length > MAX_BODY:
                self._send(413, "Too long", "text/plain; charset=utf-8")
                return
            form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
            q = (form.get("q") or [""])[0][:MAX_QUESTION]
            self._send(200, render(ask(q), q, rooms(), checked()))

    return Handler


def serve(
    ask: Callable[[str], str], rooms: Callable[[], list[str]], port: int, checked: Callable[[], str] = lambda: ""
) -> None:
    httpd = HTTPServer(("127.0.0.1", port), make_handler(ask, rooms, port, checked))
    print(f"Ask Fleetwatch: http://127.0.0.1:{port}/  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
