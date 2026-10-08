"""The v0.2 approval page (docs/design/approved-writes.md, "The approval page", "Flow" 3-7, "Limits against approval
fatigue").

Every test runs the page in this process on 127.0.0.1 with a fake executor and fake fleet reads. Nothing here signs
in, calls Epiphan or Anthropic, or posts to Slack.
"""

import http.client
import logging
import re
import socket
import threading
from datetime import UTC, datetime, timedelta
from http.server import HTTPServer
from urllib.parse import urlencode

import pytest

from fleetwatch import approve_page
from fleetwatch.approve_page import FATIGUE_AFTER, MAX_BODY, ApprovePage, RecordingExecutor, make_handler
from fleetwatch.epiphan.executor import Outcome
from fleetwatch.model import Channel, Device, Endpoint, Event, Fleet
from fleetwatch.policy import load_tools
from fleetwatch.proposals import canonical, parse_canonical, state_fingerprint
from fleetwatch.state import State

ROOM = "0a1b2c3d"  # Room 204 Pearl Mini
OTHER = "0e0f1a2b"  # Room 105 Pearl-2
STREAM = "3f2a9c1e-5b7d-4e8f-9a0b-1c2d3e4f5a6b"  # a stream endpoint ID, fake
SECRET = "test-launch-secret-not-real-0123456789abcdef"
TOOLS = load_tools()


def _fleet(name: str = "Room 204 Pearl Mini") -> Fleet:
    now = datetime.now(UTC)
    fleet = Fleet(taken_at=now)
    fleet.devices[ROOM] = Device(ROOM, name, "Pearl Mini", channels={"1": Channel("1", "Lecture")})
    fleet.devices[OTHER] = Device(OTHER, "Room 105 Pearl-2", "Pearl-2", online=False)
    fleet.endpoints = {STREAM: Endpoint(STREAM, "Rehearsal stream", "rehearsal.example.invalid")}
    return fleet


class FakeExecutor:
    """Stands in for WriteExecutor: claims the record like the real one, records it, and says ok."""

    def __init__(self, state: State, outcome: Outcome | None = None):
        self.state = state
        self.calls: list = []
        self.outcome = outcome or Outcome("ok", "Epiphan reported no errors")

    async def execute(self, record) -> Outcome:
        aid = self.state.claim(record)
        assert aid is not None
        self.calls.append(record)
        self.state.record_outcome(aid, self.outcome.status if self.outcome.sent else "error", self.outcome.detail)
        return self.outcome


class Harness:
    def __init__(self, port, state, executor, page, fleet_box):
        self.port, self.state, self.executor, self.page, self.fleet_box = port, state, executor, page, fleet_box

    def add(self, tool="batch_reboot", args=None, targets=(ROOM,), reason="Room 204 looks stuck."):
        args = args if args is not None else {"device_ids": [ROOM]}
        fp = state_fingerprint(self.fleet_box[0], list(targets))
        version = TOOLS.propose[tool].schema.version
        return self.state.add_proposal(tool, args, list(targets), fp, version, "sandbox", reason)


@pytest.fixture
def harness():
    port = _free_port()
    state = State(":memory:", check_same_thread=False)
    executor = FakeExecutor(state)
    fleet_box = [_fleet()]
    clock = [1000.0]

    async def read_fleet() -> Fleet:
        if isinstance(fleet_box[0], Exception):
            raise fleet_box[0]
        return fleet_box[0]

    asked: list[str] = []

    def ask_fn(q: str) -> str:
        asked.append(q)
        return f"You asked <{q}>"

    page = ApprovePage(state, read_fleet, executor, TOOLS, port, ask_fn=ask_fn, secret=SECRET, clock=lambda: clock[0])
    httpd = HTTPServer(("127.0.0.1", port), make_handler(page))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    h = Harness(port, state, executor, page, fleet_box)
    h.clock, h.asked = clock, asked
    yield h
    httpd.shutdown()
    httpd.server_close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _req(port, method, path, fields=None, cookie=None, site="same-origin", origin=None, host=None, raw=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Host": host or f"127.0.0.1:{port}"}
    if cookie:
        headers["Cookie"] = cookie
    if site:
        headers["Sec-Fetch-Site"] = site
    if origin:
        headers["Origin"] = origin
    body = raw if raw is not None else (urlencode(fields) if fields is not None else None)
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    c.request(method, path, body=body, headers=headers)
    r = c.getresponse()
    return r.status, r.read().decode(), r.getheaders()


def _header(headers, name):
    return [v for k, v in headers if k.lower() == name.lower()]


def _login(h, secret=SECRET) -> str:
    status, _, headers = _req(h.port, "POST", "/login", {"secret": secret})
    assert status == 303, status
    cookie = _header(headers, "Set-Cookie")[0]
    return cookie.split(";")[0]


def _forms(body: str) -> dict[str, dict[str, str]]:
    """action -> hidden fields, for every form on the page."""
    out = {}
    for m in re.finditer(r'<form[^>]*action="([^"]+)"[^>]*>(.*?)</form>', body, re.DOTALL):
        out[m.group(1)] = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', m.group(2)))
    return out


def _rows(state: State, table: str):
    return [tuple(r) for r in state.db.execute(f"SELECT * FROM {table}")]


# --- sign-in to the page -----------------------------------------------------------------------------


def test_no_session_gives_the_secret_form(harness):
    harness.add()
    status, body, _ = _req(harness.port, "GET", "/")
    assert status == 200
    assert 'name="secret"' in body and 'type="password"' in body
    assert "batch_reboot" not in body and "Room 204" not in body


def test_right_secret_sets_a_strict_http_only_session_cookie_that_isnt_the_secret(harness):
    status, _, headers = _req(harness.port, "POST", "/login", {"secret": SECRET})
    assert status == 303
    assert _header(headers, "Location") == ["/"]
    cookie = _header(headers, "Set-Cookie")[0]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    assert SECRET not in cookie


def test_wrong_secrets_slow_down_instead_of_locking_for_good(harness):
    # Three free tries, then a growing wait. Entries made during the wait aren't checked and don't extend it.
    for _ in range(3):
        status, body, _ = _req(harness.port, "POST", "/login", {"secret": "nope"})
        assert status == 403 and "isn't right" in body
    status, _, _ = _req(harness.port, "POST", "/login", {"secret": "nope"})
    assert status == 403  # the 4th wrong one starts a 2 second wait
    status, body, headers = _req(harness.port, "POST", "/login", {"secret": SECRET})
    assert status == 429 and "wait" in body.lower() and not _header(headers, "Set-Cookie")
    harness.clock[0] += 2.5
    assert _login(harness)


def test_a_guesser_hammering_the_form_cannot_extend_the_wait(harness):
    for _ in range(4):
        _req(harness.port, "POST", "/login", {"secret": "nope"})
    for _ in range(50):  # all during the wait: refused, not counted
        assert _req(harness.port, "POST", "/login", {"secret": "nope"})[0] == 429
        harness.clock[0] += 0.03
    harness.clock[0] += 1.0  # the 2 second wait is over
    assert _login(harness)


def test_the_wait_is_capped_so_a_correct_secret_always_works_later(harness):
    page = harness.page
    for _ in range(40):
        harness.clock[0] += 120  # always past the wait
        assert page.login("nope")[0] == 403
    harness.clock[0] += approve_page.LOCKOUT_SECONDS + 1
    assert page.login(SECRET)[0] == 303


def test_lockout_is_per_client_address(harness):
    page = harness.page
    for _ in range(8):
        harness.clock[0] += 120
        page.login("nope", client="10.0.0.9")
    page.login("nope", client="10.0.0.9")
    assert page.login(SECRET, client="10.0.0.9")[0] == 429
    assert page.login(SECRET, client="10.0.0.10")[0] == 303  # another address isn't slowed by it


def test_a_global_ceiling_slows_every_address_but_only_for_a_while(harness):
    page = harness.page
    for i in range(approve_page.GLOBAL_WRONG):  # many addresses, one wrong try each: none is slowed alone
        assert page.login("nope", client=f"10.1.0.{i}")[0] == 403
    assert page.login(SECRET, client="10.2.0.1")[0] == 429
    harness.clock[0] += approve_page.LOCKOUT_SECONDS + 1
    assert page.login(SECRET, client="10.2.0.1")[0] == 303


def test_a_right_secret_resets_that_addresses_count(harness):
    page = harness.page
    for _ in range(3):
        page.login("nope")
    assert page.login(SECRET)[0] == 303
    for _ in range(3):
        assert page.login("nope")[0] == 403  # free tries again, no wait


def test_login_needs_same_origin_too(harness):
    status, _, headers = _req(harness.port, "POST", "/login", {"secret": SECRET}, site="cross-site")
    assert status == 403 and not _header(headers, "Set-Cookie")


def test_forged_cookie_gets_the_secret_form(harness):
    harness.add()
    status, body, _ = _req(harness.port, "GET", "/", cookie="fleetwatch_approve=made-up")
    assert status == 200 and 'name="secret"' in body and "batch_reboot" not in body


# --- headers and hosts -------------------------------------------------------------------------------


def test_headers_are_set_on_every_page(harness):
    for status, _, headers in (
        _req(harness.port, "GET", "/"),
        _req(harness.port, "POST", "/login", {"secret": "nope"}),
    ):
        csp = _header(headers, "Content-Security-Policy")[0]
        assert (
            csp
            == "default-src 'none'; base-uri 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"
        )
        assert _header(headers, "X-Frame-Options") == ["DENY"]
        assert _header(headers, "Referrer-Policy") == ["no-referrer"]
        assert _header(headers, "Cache-Control") == ["no-store"]


def test_foreign_host_is_refused(harness):
    status, _, _ = _req(harness.port, "GET", "/", host="evil.example:80")
    assert status == 403


def test_body_is_capped(harness):
    status, _, _ = _req(harness.port, "POST", "/login", raw="secret=" + "a" * (MAX_BODY + 1))
    assert status == 413


# --- the card ----------------------------------------------------------------------------------------


def _card(h):
    cookie = _login(h)
    status, body, _ = _req(h.port, "GET", "/", cookie=cookie)
    assert status == 200
    return cookie, body


def test_card_shows_every_argument_and_the_target_facts(harness):
    harness.add(
        "start_stream_endpoint",
        {"stream_id": STREAM, "device_id": ROOM, "channel_id": "1"},
    )
    _, body = _card(harness)
    assert "Start streaming to a destination" in body and "start_stream_endpoint" in body
    for value in (STREAM, ROOM, "channel_id", "stream_id", "device_id"):
        assert value in body
    assert "Room 204 Pearl Mini" in body and "Pearl Mini" in body
    assert "Online" in body and "Not recording" in body
    assert "To undo it, stop the stream." in body


def test_card_escapes_an_injected_device_name(harness):
    harness.add()
    harness.fleet_box[0] = _fleet(name='<script>alert(1)</script> "Approve now"')
    _, body = _card(harness)
    assert "<script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &quot;Approve now&quot;" in body


def test_hidden_characters_in_a_device_name_are_shown_as_escapes(harness):
    harness.add()
    harness.fleet_box[0] = _fleet(name="Room 204\u202e\u200b")
    _, body = _card(harness)
    assert "\u202e" not in body and "\u200b" not in body
    assert "Room 204\\u202e\\u200b" in body


def test_reason_is_in_its_own_labelled_box_escaped_and_capped(harness):
    harness.add(reason="<b>trust me</b> " + "x" * 900)
    _, body = _card(harness)
    m = re.search(r'<section class="reason">(.*?)</section>', body, re.DOTALL)
    assert m and "Written by the assistant, not checked" in m.group(1)
    assert "&lt;b&gt;trust me&lt;/b&gt;" in m.group(1) and "<b>trust me" not in body
    text = re.search(r"<pre>(.*?)</pre>", m.group(1), re.DOTALL).group(1)
    assert len(text.replace("&lt;", "<").replace("&gt;", ">")) <= 500


def test_one_card_at_a_time_oldest_first_with_a_count(harness):
    first = harness.add()
    harness.add("batch_reboot", {"device_ids": [OTHER]}, targets=(OTHER,), reason="second")
    _, body = _card(harness)
    assert _forms(body)["/deny"]["proposal"] == str(first)
    assert body.count('action="/deny"') == 1
    assert "1 more waiting" in body


def test_deny_is_the_focused_default_and_approve_never_is(harness):
    harness.add()
    _, body = _card(harness)
    deny = re.search(r'<form[^>]*action="/deny".*?</form>', body, re.DOTALL).group(0)
    approve = re.search(r'<form[^>]*action="/approve".*?</form>', body, re.DOTALL).group(0)
    assert "autofocus" in deny and "autofocus" not in approve
    assert body.index('action="/deny"') < body.index('action="/approve"')


def test_an_argument_that_cant_be_shown_in_full_gives_only_deny(harness):
    harness.add("confirm_cms_event_on_device", {"device_id": ROOM, "event_id": "ok-id", "note": "a\u202eb"})
    _, body = _card(harness)
    assert "This change can't be shown in full, so it can't be approved" in body
    assert 'action="/approve"' not in body and 'action="/deny"' in body


def test_a_target_not_on_the_fresh_list_gives_only_deny(harness):
    harness.add()
    harness.fleet_box[0] = Fleet(taken_at=datetime.now(UTC))
    _, body = _card(harness)
    assert 'action="/approve"' not in body and 'action="/deny"' in body


def test_a_failed_fresh_read_gives_only_deny(harness):
    harness.add()
    harness.fleet_box[0] = RuntimeError("Epiphan is having trouble")
    _, body = _card(harness)
    assert "couldn't read" in body.lower() and 'action="/approve"' not in body


def test_get_never_changes_state(harness):
    harness.add()
    before = (_rows(harness.state, "proposals"), _rows(harness.state, "approvals"))
    cookie = _login(harness)
    for path in ("/", "/approve", "/deny", "/confirm", "/?proposal=1&approve=1"):
        _req(harness.port, "GET", path, cookie=cookie)
    assert (_rows(harness.state, "proposals"), _rows(harness.state, "approvals")) == before
    assert harness.executor.calls == []


def test_spanish_page_when_the_browser_asks(harness):
    harness.add("batch_firmware_update", {"device_ids": [ROOM]})
    cookie = _login(harness)
    c = http.client.HTTPConnection("127.0.0.1", harness.port, timeout=5)
    c.request("GET", "/", headers={"Host": f"127.0.0.1:{harness.port}", "Cookie": cookie, "Accept-Language": "es-MX"})
    body = c.getresponse().read().decode()
    assert 'lang="es"' in body and "Actualización de firmware" in body
    assert "el firmware anterior no se guarda" in body


# --- CSRF --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "site, origin",
    [
        (None, None),  # no Sec-Fetch-Site, no Origin
        ("cross-site", None),
        ("same-site", None),
        ("none", None),
        (None, "http://evil.example"),
        ("same-origin", "http://evil.example"),
        (None, "null"),
    ],
)
def test_cross_site_post_is_refused(harness, site, origin):
    pid = harness.add()
    cookie, body = _card(harness)
    fields = _forms(body)["/deny"]
    status, _, _ = _req(harness.port, "POST", "/deny", fields, cookie=cookie, site=site, origin=origin)
    assert status == 403
    assert harness.state.db.execute("SELECT status FROM proposals WHERE id=?", (pid,)).fetchone()[0] == "pending"


def test_origin_alone_is_enough_when_the_browser_sends_no_sec_fetch_site(harness):
    harness.add()
    cookie, body = _card(harness)
    status, _, _ = _req(
        harness.port,
        "POST",
        "/deny",
        _forms(body)["/deny"],
        cookie=cookie,
        site=None,
        origin=f"http://127.0.0.1:{harness.port}",
    )
    assert status == 200


@pytest.mark.parametrize("token", [None, "", "0" * 64, "wrong"])
def test_missing_or_wrong_token_is_refused(harness, token):
    pid = harness.add()
    cookie, body = _card(harness)
    fields = dict(_forms(body)["/approve"])
    if token is None:
        fields.pop("token")
    else:
        fields["token"] = token
    status, _, _ = _req(harness.port, "POST", "/approve", fields, cookie=cookie)
    assert status == 403 and harness.executor.calls == []
    assert harness.state.db.execute("SELECT status FROM proposals WHERE id=?", (pid,)).fetchone()[0] == "pending"


def test_token_is_bound_to_the_proposal(harness):
    harness.add()
    other = harness.add("batch_reboot", {"device_ids": [OTHER]}, targets=(OTHER,))
    cookie, body = _card(harness)
    fields = dict(_forms(body)["/deny"], proposal=str(other))
    status, _, _ = _req(harness.port, "POST", "/deny", fields, cookie=cookie)
    assert status == 403


def test_token_is_bound_to_the_session(harness):
    harness.add()
    _, body = _card(harness)
    other_cookie = _login(harness)
    status, _, _ = _req(harness.port, "POST", "/deny", _forms(body)["/deny"], cookie=other_cookie)
    assert status == 403


def test_post_without_a_session_is_refused(harness):
    harness.add()
    _, body = _card(harness)
    status, _, _ = _req(harness.port, "POST", "/deny", _forms(body)["/deny"])
    assert status == 403


# --- names for stream, event and channel targets (issue #99) -------------------------------------------

UNKNOWN_STREAM = "9a8b7c6d-1e2f-4a3b-8c4d-5e6f7a8b9c0d"
START = {"stream_id": STREAM, "device_id": ROOM, "channel_id": "1"}


def _only_deny(body: str) -> bool:
    forms = _forms(body)
    return "/deny" in forms and "/approve" not in forms


def test_card_shows_the_stream_endpoints_name_and_host_and_no_key(harness):
    harness.fleet_box[0].endpoints[STREAM] = Endpoint(STREAM, "Rehearsal stream", "rehearsal.example.invalid")
    harness.add("start_stream_endpoint", START)
    _, body = _card(harness)
    assert "Rehearsal stream" in body and "rehearsal.example.invalid" in body
    assert "FAKEKEY123" not in body and "rtmp://" not in body
    assert "/approve" in _forms(body)


def test_card_shows_the_channels_name_for_a_channel_argument(harness):
    harness.add("start_stream_endpoint", START)
    _, body = _card(harness)
    assert "Lecture" in body


def test_card_shows_the_channels_name_for_a_channel_target(harness):
    harness.add("batch_recording", {"action": "start", "device_ids": [f"{ROOM}-1"]})
    _, body = _card(harness)
    assert "Lecture" in body and "/approve" in _forms(body)


def test_an_unknown_channel_offers_deny_only(harness):
    harness.add("batch_recording", {"action": "start", "device_ids": [f"{ROOM}-7"]})
    _, body = _card(harness)
    assert "isn't on" in body and _only_deny(body)


def test_an_unknown_stream_id_offers_deny_only(harness):
    harness.add("start_stream_endpoint", {**START, "stream_id": UNKNOWN_STREAM})
    _, body = _card(harness)
    assert "Rehearsal stream" not in body and "isn't on the team's list" in body
    assert _only_deny(body)


def test_a_stream_list_that_couldnt_be_read_offers_deny_only(harness):
    harness.fleet_box[0].endpoints = None
    harness.add("start_stream_endpoint", START)
    _, body = _card(harness)
    assert "couldn't read" in body and _only_deny(body)


def test_card_shows_the_event_title_with_start_and_end(harness):
    start = datetime(2030, 1, 2, 15, 0, tzinfo=UTC)
    harness.fleet_box[0].events[ROOM] = Event(ROOM, "Rehearsal session", start, start + timedelta(hours=1), id="evt-1")
    harness.add("confirm_cms_event_on_device", {"device_id": ROOM, "event_id": "evt-1"})
    _, body = _card(harness)
    assert "Rehearsal session" in body and "2030-01-02 15:00" in body and "2030-01-02 16:00" in body
    assert "/approve" in _forms(body)


def test_an_event_that_isnt_the_devices_offers_deny_only(harness):
    start = datetime(2030, 1, 2, 15, 0, tzinfo=UTC)
    harness.fleet_box[0].events[ROOM] = Event(ROOM, "Rehearsal session", start, None, id="evt-1")
    harness.add("confirm_cms_event_on_device", {"device_id": ROOM, "event_id": "evt-2"})
    _, body = _card(harness)
    assert "Rehearsal session" not in body and _only_deny(body)


def test_injected_stream_and_channel_names_are_escaped_and_hidden_characters_shown(harness):
    fleet = harness.fleet_box[0]
    fleet.endpoints[STREAM] = Endpoint(STREAM, "<script>alert(1)</script>\u202eevil", "host<b>.example.invalid")
    fleet.devices[ROOM].channels["1"].name = "<img src=x onerror=alert(2)>\u200b"
    harness.add("start_stream_endpoint", START)
    _, body = _card(harness)
    assert "<script>" not in body and "<img" not in body and "<b>" not in body
    assert "&lt;script&gt;" in body and "\\u202e" in body and "\\u200b" in body
    assert "\u202e" not in body and "\u200b" not in body


def test_an_injected_event_title_is_escaped_and_hidden_characters_shown(harness):
    start = datetime(2030, 1, 2, 15, 0, tzinfo=UTC)
    harness.fleet_box[0].events[ROOM] = Event(ROOM, "<i>title</i>\u202e", start, None, id="evt-1")
    harness.add("confirm_cms_event_on_device", {"device_id": ROOM, "event_id": "evt-1"})
    _, body = _card(harness)
    assert "<i>" not in body and "&lt;i&gt;title" in body and "\\u202e" in body and "\u202e" not in body


# --- approve, confirm, deny --------------------------------------------------------------------------


def _approve_simple(h):
    pid = h.add("start_stream_endpoint", {"stream_id": STREAM, "device_id": ROOM, "channel_id": "1"})
    cookie, body = _card(h)
    return pid, cookie, _forms(body)["/approve"]


def test_approve_runs_the_executor_exactly_once_and_shows_the_outcome(harness):
    pid, cookie, fields = _approve_simple(harness)
    status, body, _ = _req(harness.port, "POST", "/approve", fields, cookie=cookie)
    assert status == 200 and "Done" in body
    assert [r.proposal_id for r in harness.executor.calls] == [pid]
    assert parse_canonical(harness.executor.calls[0].arguments) == {
        "channel_id": "1",
        "device_id": ROOM,
        "stream_id": STREAM,
    }


def test_a_second_approve_of_the_same_proposal_is_refused(harness):
    _, cookie, fields = _approve_simple(harness)
    _req(harness.port, "POST", "/approve", fields, cookie=cookie)
    status, body, _ = _req(harness.port, "POST", "/approve", fields, cookie=cookie)
    assert status == 409 and "isn't waiting for approval" in body
    assert len(harness.executor.calls) == 1


def test_outcome_errors_and_unknown_are_shown_in_plain_words_redacted(harness):
    harness.executor.outcome = Outcome(
        "error", "Epiphan reported errors on 1 device(s)", {ROOM: "busy <b> token=abcdef0123456789abcdef"}
    )
    _, cookie, fields = _approve_simple(harness)
    _, body, _ = _req(harness.port, "POST", "/approve", fields, cookie=cookie)
    assert ROOM in body and "busy &lt;b&gt;" in body and "abcdef0123456789abcdef" not in body

    harness.executor.outcome = Outcome("unknown", "Epiphan didn't answer in time; the change may or may not have run")
    harness.executor.calls.clear()
    _, cookie, fields = _approve_simple(harness)
    _, body, _ = _req(harness.port, "POST", "/approve", fields, cookie=cookie)
    assert "may or may not have run" in body


def test_disruptive_tool_needs_the_confirm_step_that_names_the_room(harness):
    pid = harness.add("batch_reboot", {"device_ids": [ROOM]})
    cookie, body = _card(harness)
    status, confirm, _ = _req(harness.port, "POST", "/approve", _forms(body)["/approve"], cookie=cookie)
    assert status == 200 and harness.executor.calls == []
    assert "Room 204 Pearl Mini" in confirm and 'action="/confirm"' in confirm
    assert harness.state.db.execute("SELECT status FROM proposals WHERE id=?", (pid,)).fetchone()[0] == "pending"
    # The card's token doesn't open the confirm step: it has its own.
    card_fields = _forms(body)["/approve"]
    status, _, _ = _req(harness.port, "POST", "/confirm", card_fields, cookie=cookie)
    assert status == 403 and harness.executor.calls == []
    # Deny stays focused on the confirm page too.
    deny = re.search(r'<form[^>]*action="/deny".*?</form>', confirm, re.DOTALL).group(0)
    assert "autofocus" in deny and "autofocus" not in re.search(
        r'<form[^>]*action="/confirm".*?</form>', confirm, re.DOTALL
    ).group(0)
    status, done, _ = _req(harness.port, "POST", "/confirm", _forms(confirm)["/confirm"], cookie=cookie)
    assert status == 200 and "Done" in done
    assert [r.proposal_id for r in harness.executor.calls] == [pid]


def test_deny_records_a_denial_and_runs_nothing(harness):
    pid = harness.add()
    cookie, body = _card(harness)
    status, page, _ = _req(harness.port, "POST", "/deny", _forms(body)["/deny"], cookie=cookie)
    assert status == 200 and "Nothing ran" in page
    assert harness.state.db.execute("SELECT status FROM proposals WHERE id=?", (pid,)).fetchone()[0] == "denied"
    assert harness.state.recent_audit("denial")[0][1]["proposal_id"] == pid
    assert harness.executor.calls == []


def _three(h):
    return [h.add(targets=(ROOM,), args={"device_ids": [ROOM]}, reason=f"r{i}") for i in range(3)]


def test_deny_all_button_shows_only_with_two_or_more_waiting(harness):
    harness.add()
    _, body = _card(harness)
    assert "/deny_all" not in _forms(body)
    harness.add(tool="batch_reboot", args={"device_ids": [ROOM]}, reason="again")
    _, body = _card(harness)
    form = _forms(body)["/deny_all"]
    assert set(form) == {"token"} and "Deny all pending" in body


def test_deny_all_denies_everything_waiting_and_runs_nothing(harness):
    ids = _three(harness)
    cookie, body = _card(harness)
    status, page, _ = _req(harness.port, "POST", "/deny_all", _forms(body)["/deny_all"], cookie=cookie)
    assert status == 200 and "3" in page
    rows = harness.state.db.execute("SELECT status FROM proposals WHERE id IN (?,?,?)", ids).fetchall()
    assert [r[0] for r in rows] == ["denied"] * 3
    assert harness.executor.calls == [] and _rows(harness.state, "approvals") == []


def test_deny_all_needs_session_same_origin_and_its_own_token(harness):
    _three(harness)
    cookie, body = _card(harness)
    form = _forms(body)["/deny_all"]
    assert _req(harness.port, "POST", "/deny_all", form)[0] == 403  # no session
    assert _req(harness.port, "POST", "/deny_all", form, cookie=cookie, site="cross-site")[0] == 403
    assert _req(harness.port, "POST", "/deny_all", {"token": "x"}, cookie=cookie)[0] == 403
    deny_token = _forms(body)["/deny"]["token"]  # a per-proposal token is not a deny-all token
    assert _req(harness.port, "POST", "/deny_all", {"token": deny_token}, cookie=cookie)[0] == 403
    assert len(harness.state.pending_proposals()) == 3
    other = _login(harness)  # another session's token doesn't work either
    assert _req(harness.port, "POST", "/deny_all", form, cookie=other)[0] == 403
    assert _req(harness.port, "POST", "/deny_all", form, cookie=cookie)[0] == 200


def test_there_is_no_approve_all_and_one_approve_request_approves_exactly_one(harness, monkeypatch):
    _three(harness)
    cookie, body = _card(harness)
    for path in ("/approve_all", "/approve-all", "/approve_all_pending"):
        assert _req(harness.port, "POST", path, _forms(body)["/deny_all"], cookie=cookie)[0] == 404
    calls = []
    real = harness.state.approve
    monkeypatch.setattr(harness.state, "approve", lambda *a, **k: calls.append(a) or real(*a, **k))
    status, page, _ = _req(harness.port, "POST", "/approve", _forms(body)["/approve"], cookie=cookie)
    assert status == 200 and calls == []  # a disruptive change asks for the second step first
    status, _, _ = _req(harness.port, "POST", "/confirm", _forms(page)["/confirm"], cookie=cookie)
    assert status == 200 and len(calls) == 1 and len(harness.executor.calls) == 1
    left = harness.state.db.execute("SELECT COUNT(*) FROM proposals WHERE status='pending'").fetchone()[0]
    assert left == 2


def test_the_page_says_when_the_proposal_queue_is_full_in_english_and_spanish(harness):
    harness.add()
    _, body = _card(harness)
    assert "Proposal queue full" not in body
    harness.add(tool="batch_reboot", args={"device_ids": [ROOM]}, reason="b")
    harness.add(tool="batch_recording", args={"device_ids": [ROOM], "action": "start"}, reason="c")
    cookie, body = _card(harness)
    assert "Proposal queue full" in body and "3 waiting" in body
    c = http.client.HTTPConnection("127.0.0.1", harness.port, timeout=5)
    headers = {"Host": f"127.0.0.1:{harness.port}", "Cookie": cookie, "Accept-Language": "es"}
    c.request("GET", "/", headers=headers)
    assert "Cola de propuestas llena" in c.getresponse().read().decode()


def test_the_page_says_when_the_hourly_cap_is_reached(harness):
    from fleetwatch.state import PROPOSALS_PER_HOUR

    for i in range(PROPOSALS_PER_HOUR):
        harness.state.db.execute(
            "INSERT INTO proposals (created_at, tool, arguments, targets, fingerprint, schema_version, slot, status)"
            " VALUES (CAST(strftime('%s','now') AS INTEGER), 'batch_reboot', '{}', '[]', '{}', 1, 'sandbox', 'expired')"
        )
    harness.state.db.commit()
    _, body = _card(harness)
    assert "Proposal queue full" in body and f"{PROPOSALS_PER_HOUR} in the last hour" in body


def test_card_that_cant_be_shown_cant_be_approved_even_with_a_token(harness):
    pid = harness.add("confirm_cms_event_on_device", {"device_id": ROOM, "event_id": "ok-id", "note": "a\u200bb"})
    cookie, body = _card(harness)
    deny = _forms(body)["/deny"]
    status, _, _ = _req(harness.port, "POST", "/approve", deny, cookie=cookie)
    assert status == 409 and harness.executor.calls == []
    assert harness.state.db.execute("SELECT status FROM proposals WHERE id=?", (pid,)).fetchone()[0] == "pending"


def test_fatigue_note_after_several_approvals(harness):
    cookie = _login(harness)
    for n in range(2, FATIGUE_AFTER + 1):  # the cards name each channel, so the unit needs them all
        harness.fleet_box[0].devices[ROOM].channels[str(n)] = Channel(str(n), f"Channel {n}")
    for i in range(FATIGUE_AFTER):
        harness.add("start_stream_endpoint", {"stream_id": STREAM, "device_id": ROOM, "channel_id": str(i + 1)})
        _, body, _ = _req(harness.port, "GET", "/", cookie=cookie)
        assert "changes in this session" not in body
        _, done, _ = _req(harness.port, "POST", "/approve", _forms(body)["/approve"], cookie=cookie)
    assert f"You've approved {FATIGUE_AFTER} changes in this session." in done
    harness.add()
    _, body, _ = _req(harness.port, "GET", "/", cookie=cookie)
    assert f"You've approved {FATIGUE_AFTER} changes in this session." in body


def test_expire_all_pending_runs_on_start():
    state = State(":memory:", check_same_thread=False)
    fp = state_fingerprint(_fleet(), [ROOM])
    pid = state.add_proposal("batch_reboot", {"device_ids": [ROOM]}, [ROOM], fp, 1, "sandbox", "old")

    async def read_fleet():
        return _fleet()

    ApprovePage(state, read_fleet, FakeExecutor(state), TOOLS, 1, secret=SECRET)
    assert state.db.execute("SELECT status FROM proposals WHERE id=?", (pid,)).fetchone()[0] == "expired"
    assert state.recent_audit("expiry")


# --- the chat box hook -------------------------------------------------------------------------------


def test_chat_box_posts_to_ask_with_the_same_csrf_rules(harness):
    cookie, body = _card(harness)
    fields = _forms(body)["/ask"]
    status, _, _ = _req(harness.port, "POST", "/ask", {**fields, "q": "hi"}, cookie=cookie, site="cross-site")
    assert status == 403 and harness.asked == []
    status, _, _ = _req(harness.port, "POST", "/ask", {"q": "hi", "token": "x"}, cookie=cookie)
    assert status == 403 and harness.asked == []
    status, page, _ = _req(harness.port, "POST", "/ask", {**fields, "q": "<i>hi</i>"}, cookie=cookie)
    assert status == 200 and harness.asked == ["<i>hi</i>"]
    assert "You asked &lt;&lt;i&gt;hi&lt;/i&gt;&gt;" in page
    reply = re.search(r'<section class="reason">.*?</section>', page, re.DOTALL).group(0)
    assert "Written by the assistant, not checked" in reply and "You asked" in reply


def test_the_handler_has_a_socket_timeout():
    from fleetwatch.approve_page import make_handler

    page = ApprovePage(State(":memory:"), None, None, TOOLS, 0, secret=SECRET)
    assert 0 < make_handler(page).timeout <= 30


def test_no_ask_fn_hides_the_chat_box():
    state = State(":memory:", check_same_thread=False)

    async def read_fleet():
        return _fleet()

    page = ApprovePage(state, read_fleet, FakeExecutor(state), TOOLS, 1, secret=SECRET)
    assert 'action="/ask"' not in page.render_card_page(page.new_session(), "en")


# --- the secret stays out of logs and URLs -----------------------------------------------------------


def test_secret_never_appears_in_logs_urls_or_the_audit(harness, caplog):
    caplog.set_level(logging.DEBUG)
    harness.add()
    cookie, body = _card(harness)
    _req(harness.port, "POST", "/login", {"secret": "wrong"})
    _req(harness.port, "POST", "/deny", _forms(body)["/deny"], cookie=cookie)
    assert SECRET not in caplog.text
    assert SECRET not in body and SECRET not in cookie
    assert all(SECRET not in str(r) for r in _rows(harness.state, "audit"))
    assert re.findall(r'(?:href|action)="([^"]*)"', body) and all(
        SECRET not in u for u in re.findall(r'(?:href|action)="([^"]*)"', body)
    )


def test_serve_prints_the_secret_once_and_the_url_without_it(monkeypatch, capsys):
    state = State(":memory:", check_same_thread=False)

    async def read_fleet():
        return _fleet()

    page = ApprovePage(state, read_fleet, FakeExecutor(state), TOOLS, _free_port(), secret=SECRET)

    class NoServer:
        def __init__(self, *a, **k):
            pass

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(approve_page, "HTTPServer", NoServer)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    approve_page.serve(page)
    out = capsys.readouterr().out
    assert out.count(SECRET) == 1
    url = re.search(r"http://\S+", out).group(0)
    assert SECRET not in url and url.startswith("http://127.0.0.1:")


def _serve_page(monkeypatch, tmp_path, tty: bool, **kw):
    state = State(":memory:", check_same_thread=False)

    async def read_fleet():
        return _fleet()

    page = ApprovePage(state, read_fleet, FakeExecutor(state), TOOLS, _free_port(), secret=SECRET)
    seen = {}

    class NoServer:
        def __init__(self, *a, **k):
            pass

        def serve_forever(self):
            f = tmp_path / approve_page.SECRET_FILE
            seen["exists"] = f.exists()
            seen["mode"] = f.stat().st_mode & 0o777 if f.exists() else None
            seen["text"] = f.read_text() if f.exists() else None
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(approve_page, "HTTPServer", NoServer)
    monkeypatch.setattr("sys.stdout.isatty", lambda: tty, raising=False)
    approve_page.serve(page, state_dir=tmp_path, **kw)
    return seen


def test_without_a_terminal_the_secret_goes_to_a_private_file_and_not_to_stdout(monkeypatch, tmp_path, capsys):
    seen = _serve_page(monkeypatch, tmp_path, tty=False)
    out = capsys.readouterr().out
    assert SECRET not in out and str(tmp_path / approve_page.SECRET_FILE) in out
    assert seen["exists"] and seen["mode"] == 0o600 and seen["text"].strip() == SECRET
    assert not (tmp_path / approve_page.SECRET_FILE).exists(), "deleted on a clean shutdown"


def test_on_a_terminal_the_secret_prints_and_no_file_is_made(monkeypatch, tmp_path, capsys):
    seen = _serve_page(monkeypatch, tmp_path, tty=True)
    assert capsys.readouterr().out.count(SECRET) == 1
    assert not seen["exists"]


def test_a_stale_secret_file_is_replaced_at_start_and_never_world_readable(monkeypatch, tmp_path):
    old = tmp_path / approve_page.SECRET_FILE
    old.write_text("old-secret")
    old.chmod(0o666)
    seen = _serve_page(monkeypatch, tmp_path, tty=False)
    assert seen["text"].strip() == SECRET and seen["mode"] == 0o600


def test_the_secret_file_is_created_exclusively_with_mode_600(monkeypatch, tmp_path):
    calls = []
    real = approve_page.os.open

    def spy(path, flags, mode=0o777, **k):
        calls.append((str(path), flags, mode))
        return real(path, flags, mode, **k)

    monkeypatch.setattr(approve_page.os, "open", spy)
    _serve_page(monkeypatch, tmp_path, tty=False)
    ((_, flags, mode),) = [c for c in calls if c[0].endswith(approve_page.SECRET_FILE)]
    assert flags & approve_page.os.O_EXCL and flags & approve_page.os.O_CREAT and mode == 0o600


def test_generated_secret_is_long_and_random():
    state = State(":memory:", check_same_thread=False)

    async def read_fleet():
        return _fleet()

    a = ApprovePage(state, read_fleet, FakeExecutor(state), TOOLS, 1)
    b = ApprovePage(state, read_fleet, FakeExecutor(state), TOOLS, 1)
    assert len(a.secret) >= 40 and a.secret != b.secret


# --- replay ------------------------------------------------------------------------------------------


def test_recording_executor_records_and_never_writes():
    state = State(":memory:", check_same_thread=False)
    fp = state_fingerprint(_fleet(), [ROOM])
    pid = state.add_proposal("batch_reboot", {"device_ids": [ROOM]}, [ROOM], fp, 1, "replay", "")
    record = state.consume(state.approve(pid, "s"))
    rec = RecordingExecutor(state)
    import asyncio

    out = asyncio.run(rec.execute(record))
    assert out.status == "ok" and "Nothing was sent" in out.detail
    assert rec.calls == [("batch_reboot", canonical({"device_ids": [ROOM]}))]
    assert asyncio.run(rec.execute(record)).status == "refused"  # claimed once only
    assert len(rec.calls) == 1


# --- the card says when the executor would refuse (#100) -----------------------------------------------------------
def _soon(minutes: float):
    from datetime import timedelta

    from fleetwatch.model import Event

    return Event(ROOM, "Weekly review", datetime.now(UTC) + timedelta(minutes=minutes))


def _get(harness, language=None):
    cookie = _login(harness)
    headers = {"Host": f"127.0.0.1:{harness.port}", "Cookie": cookie}
    if language:
        headers["Accept-Language"] = language
    c = http.client.HTTPConnection("127.0.0.1", harness.port, timeout=5)
    c.request("GET", "/", headers=headers)
    return c.getresponse().read().decode()


REC_STOP = {"action": "stop", "device_ids": [f"{ROOM}-1"]}
REC_START = {"action": "start", "device_ids": [f"{ROOM}-1"]}


def test_card_warns_when_a_stop_would_be_blocked_by_an_event_starting_soon(harness):
    harness.fleet_box[0].events[ROOM] = _soon(12.5)
    harness.add("batch_recording", REC_STOP)
    body = _get(harness)
    assert "starts in 13 minutes" in body and "would be blocked" in body
    assert 'action="/approve"' in body, "information, not a new gate: it can still be approved"


def test_card_does_not_warn_for_a_start_before_an_event(harness):
    harness.fleet_box[0].events[ROOM] = _soon(12)
    harness.add("batch_recording", REC_START)
    body = _get(harness)
    assert "would be blocked" not in body and "disruptive" not in body
    assert 'action="/approve"' in body


def test_card_warns_when_the_room_is_recording(harness):
    harness.fleet_box[0].devices[ROOM].channels["1"].recording = True
    harness.add("batch_reboot", {"device_ids": [ROOM]})
    body = _get(harness)
    assert "is recording, so this change would be blocked" in body


def test_card_warns_when_an_event_is_on_now(harness):
    harness.fleet_box[0].events[ROOM] = _soon(-5)
    harness.add("batch_reboot", {"device_ids": [ROOM]})
    assert "has an event on now, so this change would be blocked" in _get(harness)


def test_card_uses_the_default_window_for_a_reboot(harness):
    harness.fleet_box[0].events[ROOM] = _soon(60)
    harness.add("batch_reboot", {"device_ids": [ROOM]})
    assert "would be blocked" not in _get(harness), "60 minutes is outside the usual 30"


def test_card_uses_the_firmware_update_window(harness):
    harness.fleet_box[0].events[ROOM] = _soon(60)
    harness.add("batch_firmware_update", {"device_ids": [ROOM]})
    assert "would be blocked" in _get(harness), "firmware's window is 120"


def test_card_says_when_no_device_returned_a_schedule(harness):
    harness.add("batch_reboot", {"device_ids": [ROOM]})  # the harness fleet has no events at all
    body = _get(harness)
    assert "No event schedule came back for any device" in body
    assert 'action="/approve"' in body, "information, not a new gate"
    harness.fleet_box[0].events[OTHER] = _soon(300)
    assert "No event schedule came back" not in _get(harness), "one device with an event shows the read works"


def test_no_schedule_note_is_only_for_disruptive_changes_and_comes_in_spanish(harness):
    harness.add("batch_recording", REC_START)
    assert "No event schedule" not in _get(harness)
    harness.add("batch_reboot", {"device_ids": [ROOM]})
    harness.state.deny(harness.state.pending_proposals()[0][0], "x")
    assert "Ningún horario de eventos" in _get(harness, "es")


def test_card_does_not_warn_when_the_room_is_free(harness):
    harness.add("batch_reboot", {"device_ids": [ROOM]})
    assert "would be blocked" not in _get(harness)


def test_card_warning_in_spanish(harness):
    harness.fleet_box[0].events[ROOM] = _soon(12.5)
    harness.add("batch_recording", REC_STOP)
    body = _get(harness, "es-MX")
    assert "empieza en 13 minutos" in body and "se bloquearía" in body


def test_card_warning_escapes_the_room_name(harness):
    harness.fleet_box[0].devices[ROOM].name = "<b>Room</b> & co"
    harness.fleet_box[0].events[ROOM] = _soon(12)
    harness.add("batch_reboot", {"device_ids": [ROOM]})
    body = _get(harness)
    assert "would be blocked" in body and "<b>Room</b>" not in body and "&lt;b&gt;Room&lt;/b&gt;" in body


def test_card_does_not_warn_for_stopping_a_manual_recording(harness):
    harness.fleet_box[0].devices[ROOM].channels["1"].recording = True
    harness.add("batch_recording", REC_STOP)
    body = _get(harness)
    assert "would be blocked" not in body and 'action="/approve"' in body


def test_card_warns_for_stopping_a_recording_while_an_event_is_on(harness):
    harness.fleet_box[0].devices[ROOM].channels["1"].recording = True
    harness.fleet_box[0].events[ROOM] = _soon(-5)
    harness.add("batch_recording", REC_STOP)
    body = _get(harness)
    assert "has an event on now, so this change would be blocked" in body and "is recording" not in body
