"""A live run must not go silently wrong: token refresh, failed reads, reconnects, unknown shapes, stale answers.

Everything here is offline. The Epiphan client is faked below the guard; no sign-in, no network.
"""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from fleetwatch.epiphan import parse
from fleetwatch.epiphan.mcp import EpiphanClient, SignInExpired
from fleetwatch.epiphan.parse import apply_events, apply_system_status, parse_devices
from fleetwatch.epiphan.token_store import FileTokenStorage
from fleetwatch.heartbeat import FailedRead, tick
from fleetwatch.model import Finding, Priority
from fleetwatch.policy import Policy, load_tool_policy
from fleetwatch.state import State
from tests.conftest import NOW
from tests.test_heartbeat_replay import Capture, replay_client

ROOT = Path(__file__).resolve().parents[1]
URL = "https://mcp.example.invalid/mcp"
POLICY = Policy(quiet_start=None, quiet_end=None)


@pytest.fixture(autouse=True)
def _fresh_parse_warnings():
    parse.reset_warnings()
    yield
    parse.reset_warnings()


# --- 1. token expiry and the in-band 401 --------------------------------------------------------------------
def _client_info() -> OAuthClientInformationFull:
    return OAuthClientInformationFull(client_id="FAKECLIENT", redirect_uris=["http://localhost:8765/callback"])


async def test_expires_at_round_trips_through_the_store(tmp_path: Path):
    s = FileTokenStorage(tmp_path / "t.json")
    before = time.time()
    await s.set_tokens(OAuthToken(access_token="FAKEACCESS", refresh_token="FAKEREFRESH", expires_in=3600))
    got = s.expires_at()
    assert got is not None and got.tzinfo is not None
    assert before + 3600 - 2 <= got.timestamp() <= time.time() + 3600 + 2
    saved = json.loads((tmp_path / "t.json").read_text())
    assert saved["expires_at"].endswith("Z"), "absolute UTC, next to the token"
    assert FileTokenStorage(tmp_path / "t.json").expires_at() == got, "a new process reads the same expiry"

    await s.set_tokens(OAuthToken(access_token="FAKEACCESS2"))  # no expires_in: no stale expiry left behind
    assert s.expires_at() is None


async def test_restart_restores_expiry_so_an_expired_token_refreshes_first(tmp_path: Path):
    """After a restart the SDK knows the expiry again: an expired stored token refreshes before the request."""
    from fleetwatch.epiphan.auth import make_provider

    s = FileTokenStorage(tmp_path / "t.json")
    await s.set_tokens(OAuthToken(access_token="FAKEOLD", refresh_token="FAKEREFRESH", expires_in=1))
    await s.set_client_info(_client_info())
    data = json.loads((tmp_path / "t.json").read_text())
    data["expires_at"] = "2020-01-01T00:00:00Z"  # long expired
    (tmp_path / "t.json").write_text(json.dumps(data))

    provider = make_provider(URL, FileTokenStorage(tmp_path / "t.json"), 8765, interactive=False)
    flow = provider._auth_flow(httpx2.Request("POST", URL))
    first = await flow.__anext__()
    assert first.method == "POST" and first.url.path.endswith("/token"), "refresh goes out before the call"
    assert b"grant_type=refresh_token" in first.content and b"FAKEREFRESH" in first.content
    await flow.aclose()


async def test_a_fresh_stored_token_is_used_without_refresh(tmp_path: Path):
    from fleetwatch.epiphan.auth import make_provider

    s = FileTokenStorage(tmp_path / "t.json")
    await s.set_tokens(OAuthToken(access_token="FAKEFRESH", refresh_token="FAKEREFRESH", expires_in=3600))
    await s.set_client_info(_client_info())
    provider = make_provider(URL, FileTokenStorage(tmp_path / "t.json"), 8765, interactive=False)
    flow = provider._auth_flow(httpx2.Request("POST", URL))
    first = await flow.__anext__()
    assert str(first.url) == URL and first.headers["Authorization"] == "Bearer FAKEFRESH"
    assert provider.context.token_expiry_time and provider.context.token_expiry_time > time.time()
    await flow.aclose()


class _FakeSDK:
    """Stands in for the SDK `Client`: answers `call_tool` from a list of (is_error, text)."""

    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    async def call_tool(self, tool, arguments):
        self.calls += 1
        is_error, text = self.answers[min(self.calls, len(self.answers)) - 1]
        return SimpleNamespace(content=[SimpleNamespace(text=text)], is_error=is_error, structured_content=None)


async def _signed_in_client(tmp_path: Path) -> EpiphanClient:
    s = FileTokenStorage(tmp_path / "t.json")
    await s.set_tokens(OAuthToken(access_token="FAKEACCESS", refresh_token="FAKEREFRESH", expires_in=3600))
    await s.set_client_info(_client_info())
    c = EpiphanClient(URL, load_tool_policy(ROOT / "tool_policy.yaml"), storage=s)
    await c._provider._initialize()  # what the first request through the auth flow does
    return c


UNAUTHORIZED = (True, "Error: 401 Unauthorized")


async def test_in_band_401_refreshes_once_and_retries_once(tmp_path: Path):
    c = await _signed_in_client(tmp_path)
    c._client = _FakeSDK([UNAUTHORIZED, (False, '{"devices": []}')])
    refreshes = []
    real = c._force_refresh

    async def spy() -> bool:
        refreshes.append(1)
        return await real()

    c._force_refresh = spy
    assert await c.call("get_devices_in_my_team") == {"devices": []}
    assert c._client.calls == 2 and refreshes == [1]
    assert not c._provider.context.is_token_valid(), "marked expired, so the retry's auth flow refreshes first"


async def test_in_band_401_twice_gives_up_without_looping(tmp_path: Path):
    c = await _signed_in_client(tmp_path)
    c._client = _FakeSDK([UNAUTHORIZED])  # every call says 401
    with pytest.raises(SignInExpired):
        await c.call("get_devices_in_my_team")
    assert c._client.calls == 2, "one call, one retry, never more"


async def test_401_with_nothing_to_refresh_does_not_retry(tmp_path: Path):
    c = EpiphanClient(URL, load_tool_policy(ROOT / "tool_policy.yaml"), static_token="FAKESTATIC")
    c._client = _FakeSDK([UNAUTHORIZED])
    with pytest.raises(SignInExpired):
        await c.call("get_devices_in_my_team")
    assert c._client.calls == 1


async def test_a_device_named_401_is_not_an_auth_error(tmp_path: Path):
    """Untrusted names never pick a code path: a successful JSON result is never read as a 401."""
    c = await _signed_in_client(tmp_path)
    body = json.dumps({"devices": [{"Id": "x", "Name": "Room 401 Unauthorized Pearl Mini"}]})
    c._client = _FakeSDK([(False, body)])
    assert (await c.call("get_devices_in_my_team"))["devices"][0]["Name"].startswith("Room 401")
    assert c._client.calls == 1


def test_doctor_shows_token_expiry_without_the_token(tmp_path: Path):
    from fleetwatch.doctor import run_checks
    from tests.test_doctor import by_name, settings

    f = tmp_path / "epiphan-oauth.json"
    f.write_text(
        json.dumps(
            {
                "tokens": {"access_token": "FAKESECRETACCESS", "refresh_token": "FAKESECRETREFRESH"},
                "expires_at": "2099-06-15T12:00:00Z",
            }
        )
    )
    f.chmod(0o600)
    checks = by_name(run_checks(settings(tmp_path), reach=lambda u: True, service=lambda: ("OK", "running")))
    row = checks["Token expiry"]
    assert row.status == "OK" and "2099" in row.detail
    assert all("FAKESECRET" not in c.detail for c in checks.values())

    data = json.loads(f.read_text())
    data["expires_at"] = "2020-01-01T00:00:00Z"
    f.write_text(json.dumps(data))
    row = by_name(run_checks(settings(tmp_path), reach=lambda u: True, service=lambda: ("OK", "running")))[
        "Token expiry"
    ]
    assert row.status == "OK" and "expired" in row.detail and "refresh" in row.detail


# --- 2. a bad read is not "back to normal" -------------------------------------------------------------------
class _BadDevices:
    """The replay sample, except `get_devices_in_my_team` answers with `devices`."""

    def __init__(self, devices):
        self.inner, self.devices = replay_client(), devices

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def call(self, tool, arguments=None):
        if tool == "get_devices_in_my_team":
            return self.devices
        return await self.inner.call(tool, arguments)


async def _state_with_open_items() -> tuple[State, int]:
    state, out = State(), Capture()
    async with replay_client() as c:
        await tick(c, state, POLICY, out, first_run=True, now=NOW)
    return state, len(state.open_findings())


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({"devices": []}, id="0 devices after 31"),
        pytest.param("Internal error: upstream timed out", id="non-JSON"),
        pytest.param({"result": {"items": [{"id": "x"}]}}, id="unknown shape"),
        pytest.param([{"device_id": "x", "name": "y"}], id="list without Id"),
    ],
)
async def test_failed_read_resolves_nothing_and_posts_nothing(bad, caplog):
    state, open_before = await _state_with_open_items()
    assert open_before > 0
    snaps_before = len(state.snapshots_since(NOW - timedelta(days=1)))
    out = Capture()
    later = NOW + timedelta(minutes=3)
    with caplog.at_level(logging.WARNING), pytest.raises(FailedRead):
        await tick(_BadDevices(bad), state, POLICY, out, now=later)
    assert out.posts == [], "no post, so no 'Back to normal'"
    assert len(state.open_findings()) == open_before, "nothing resolved"
    assert len(state.snapshots_since(NOW - timedelta(days=1))) == snaps_before, "no healthy snapshot"
    assert any(r.levelno == logging.WARNING and "read" in r.getMessage().lower() for r in caplog.records)


async def test_zero_devices_on_a_first_ever_read_is_fine():
    state, out = State(), Capture()
    await tick(_BadDevices({"devices": []}), state, POLICY, out, first_run=True, now=NOW)
    assert state.last_snapshot() == NOW


# --- 3. reconnect after a failed beat, exit 75 after three in a row ------------------------------------------
class _ScriptedClient:
    """One beat per client call: `ok` or `fail`, from a shared script. Records opens and closes."""

    def __init__(self, script, log):
        self.script, self.log = script, log

    async def __aenter__(self):
        self.log.append("open")
        return self

    async def __aexit__(self, *exc):
        self.log.append("close")

    async def call(self, tool, arguments=None):
        if tool == "get_devices_in_my_team":
            step = self.script.pop(0)
            self.log.append(step)
            if step == "fail":
                raise ConnectionError("hotspot dropped")
        return await replay_client().call(tool, arguments)


class _Slash:
    def __init__(self):
        self.closed = False

    def see(self, fleet):
        pass

    def keep_alive(self):
        pass

    def close(self):
        self.closed = True


async def _run(script, state=None):
    from fleetwatch.runner import run_loop

    log: list[str] = []
    beats = len(script)

    async def sleep(_):
        if not script:
            raise asyncio.CancelledError  # end of the script: stop the loop like Ctrl+C would

    with pytest.raises((SystemExit, asyncio.CancelledError)) as e:
        await run_loop(
            lambda: _ScriptedClient(script, log),
            state or State(),
            POLICY,
            Capture(),
            first_run=True,
            sleep=sleep,
        )
    assert beats >= 1
    return log, e.value


async def test_three_failed_beats_in_a_row_exit_75():
    log, err = await _run(["fail", "fail", "fail", "ok"])
    assert isinstance(err, SystemExit) and err.code == 75
    assert log.count("fail") == 3 and "ok" not in log


async def test_a_good_beat_resets_the_failure_count():
    log, err = await _run(["fail", "fail", "ok", "fail", "fail", "ok"])
    assert not isinstance(err, SystemExit), "never three in a row"
    assert log.count("ok") == 2


async def test_failed_beat_closes_and_reopens_the_client():
    log, _ = await _run(["ok", "fail", "ok"])
    # open, ok, (same session for the next beat), fail, close, open, ok, close at the end
    assert log == ["open", "ok", "fail", "close", "open", "ok", "close"]


class _ScopedClient(_ScriptedClient):
    """Like the MCP client: it owns an anyio task group opened by the loop's task. A `stray` step makes that scope
    cancel the loop's own task from inside a read, so a CancelledError comes out although nobody asked to stop."""

    async def __aenter__(self):
        import anyio

        self.tg = anyio.create_task_group()
        await self.tg.__aenter__()
        return await super().__aenter__()

    async def __aexit__(self, *exc):
        self.tg.cancel_scope.cancel()
        await self.tg.__aexit__(*exc)
        return await super().__aexit__(*exc)

    async def call(self, tool, arguments=None):
        if tool == "get_devices_in_my_team" and self.script and self.script[0] == "stray":
            self.log.append(self.script.pop(0))
            self.tg.cancel_scope.cancel()
            await asyncio.sleep(10)
        return await super().call(tool, arguments)


async def test_a_stray_cancel_from_the_client_is_a_failed_beat_not_a_crash(caplog):
    from fleetwatch.runner import run_loop

    script, log = ["ok", "stray", "ok"], []

    async def sleep(_):
        if not script:
            raise asyncio.CancelledError  # end of the script, like Ctrl+C

    with caplog.at_level(logging.WARNING), pytest.raises(asyncio.CancelledError):
        await run_loop(lambda: _ScopedClient(script, log), State(), POLICY, Capture(), first_run=True, sleep=sleep)
    assert log.count("ok") == 2, "the beat after the stray cancel ran"
    assert log == ["open", "ok", "stray", "close", "open", "ok", "close"], "a new session after the failed beat"
    assert any("heartbeat failed (1 in a row)" in r.getMessage() for r in caplog.records)


async def test_a_real_shutdown_still_stops_the_loop_and_is_not_counted(caplog):
    from fleetwatch.runner import run_loop

    log: list[str] = []
    started = asyncio.Event()

    class Hangs(_ScriptedClient):
        async def call(self, tool, arguments=None):
            started.set()
            await asyncio.sleep(60)

    async def never(_):
        raise AssertionError("the loop went on to sleep after a shutdown")

    with caplog.at_level(logging.WARNING):
        task = asyncio.create_task(
            run_loop(lambda: Hangs([], log), State(), POLICY, Capture(), first_run=True, sleep=never)
        )
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    assert log == ["open", "close"], "the session is closed once on the way out"
    assert not any("heartbeat failed" in r.getMessage() for r in caplog.records), "a shutdown is not a failed beat"


async def test_slack_listener_still_closes_on_exit_75(monkeypatch):
    from fleetwatch import cli
    from fleetwatch.config import Settings

    slash = _Slash()
    script = ["fail", "fail", "fail"]

    async def fake_listener(*a, **k):
        return slash

    monkeypatch.setattr("fleetwatch.slack_command.start_listener", fake_listener)
    monkeypatch.setattr(cli, "_make_client", lambda settings: _ScriptedClient(script, []))
    monkeypatch.setattr(cli, "State", lambda *a, **k: State())
    monkeypatch.setattr("fleetwatch.runner._sleep", lambda s: asyncio.sleep(0))
    s = Settings(_env_file=None, policy_file=ROOT / "policy.yaml", tool_policy_file=ROOT / "tool_policy.yaml")
    with pytest.raises(SystemExit) as e:
        await cli._run(s)
    assert e.value.code == 75 and slash.closed


# --- 4. shapes the parser doesn't know -----------------------------------------------------------------------
def test_rfc3339_uptime_start_is_read(fleet):
    dev = next(iter(fleet.devices.values()))
    apply_system_status(fleet, {dev.id: {"cpu_load": 10, "uptime_start_time": "2026-10-07T14:48:00+00:00"}})
    assert fleet.system[dev.id].up_since == datetime(2026, 10, 7, 14, 48, tzinfo=UTC)


def test_rfc3339_uptime_with_z_and_camel_case(fleet):
    dev = next(iter(fleet.devices.values()))
    apply_system_status(fleet, {"devices": [{"device_id": dev.id, "system": {"uptimeStart": "2026-10-07T14:30:00Z"}}]})
    assert fleet.system[dev.id].up_since == datetime(2026, 10, 7, 14, 30, tzinfo=UTC)


def test_numeric_uptime_still_means_seconds(fleet):
    dev = next(iter(fleet.devices.values()))
    apply_system_status(fleet, {dev.id: {"uptime": "600"}})
    assert fleet.system[dev.id].up_since == fleet.taken_at - timedelta(seconds=600)


def test_unknown_event_shape_warns_once(fleet, caplog):
    dev = next(iter(fleet.devices.values()))
    weird = {"devices": {dev.id: {"upcoming": {"begins": "soon", "label": "Room 204 class"}}}}
    with caplog.at_level(logging.WARNING):
        apply_events(fleet, weird)
        apply_events(fleet, weird)
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1 and "get_current_or_next_cms_events_for_devices" in warns[0].getMessage()
    assert "Room 204" not in warns[0].getMessage(), "the warning names the tool, not untrusted content"
    assert fleet.events == {}


def test_nothing_scheduled_is_not_a_warning(fleet, caplog):
    dev = next(iter(fleet.devices.values()))
    with caplog.at_level(logging.WARNING):
        apply_events(fleet, {"devices": {dev.id: {"event": None}}})
        apply_events(fleet, {"devices": {}})
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_unknown_system_status_shape_warns(fleet, caplog):
    with caplog.at_level(logging.WARNING):
        apply_system_status(fleet, {"status": "ok", "data": "unavailable"})
    assert any("get_system_status_for_devices" in r.getMessage() for r in caplog.records)


def test_sample_parses_without_warnings(device_list, caplog):
    with caplog.at_level(logging.WARNING):
        parse_devices(device_list, NOW)
    assert not caplog.records


# --- 5. stale answers ----------------------------------------------------------------------------------------
def test_last_checked_line():
    from fleetwatch.ask import last_checked

    state = State()
    assert last_checked(state) == "Not checked yet."
    state.snapshot(NOW, 3, 3)
    assert last_checked(state, now=NOW + timedelta(minutes=5)) == f"Last checked {NOW.astimezone():%H:%M}."


async def test_ask_answer_shows_last_checked(monkeypatch, capsys):
    import sys

    from fleetwatch import cli

    monkeypatch.setattr(sys, "argv", ["fleetwatch", "ask", "what needs attention", "--replay", "tests/fixtures"])
    await asyncio.to_thread(cli.main)
    assert "Last checked " in capsys.readouterr().out


def test_ask_page_shows_last_checked():
    from fleetwatch.ask_page import render

    page = render(rooms=[], checked="Last checked 14:05.")
    assert "Last checked 14:05." in page
    assert "Not checked yet." in render(checked="Not checked yet.")


# --- 6. SQLite ------------------------------------------------------------------------------------------------
def test_file_db_uses_wal_and_a_busy_timeout(tmp_path: Path):
    s = State(tmp_path / "state.db")
    assert s.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert s.db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_memory_db_still_works():
    s = State()
    assert s.db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    s.reconcile([Finding("k", Priority.FIX_SOON, "d", "Room 204 Pearl Mini", "x")], NOW, timedelta(hours=1))
    assert len(s.open_findings()) == 1


# --- 7. the OAuth metadata discovered at login survives a restart ---------------------------------------------
AS = "https://auth.example.invalid"
TOKEN_URL = f"{AS}/oauth2/v1/token"


def _metadata() -> dict:
    return {
        "metadata": {
            "issuer": AS,
            "authorization_endpoint": f"{AS}/oauth2/v1/authorize",
            "token_endpoint": TOKEN_URL,
        },
        "auth_server_url": AS,
        "protected_resource": {"resource": URL, "authorization_servers": [AS]},
    }


async def _expired_store(tmp_path: Path, oauth: dict | None) -> FileTokenStorage:
    s = FileTokenStorage(tmp_path / "t.json")
    await s.set_tokens(OAuthToken(access_token="FAKEOLD", refresh_token="FAKEREFRESH", expires_in=1))
    await s.set_client_info(_client_info())
    if oauth is not None:
        s.set_oauth_metadata(oauth)
    data = json.loads((tmp_path / "t.json").read_text())
    data["expires_at"] = "2020-01-01T00:00:00Z"
    (tmp_path / "t.json").write_text(json.dumps(data))
    return FileTokenStorage(tmp_path / "t.json")  # a new process


def test_oauth_metadata_round_trips(tmp_path: Path):
    s = FileTokenStorage(tmp_path / "t.json")
    assert s.oauth_metadata() is None
    s.set_oauth_metadata(_metadata())
    assert FileTokenStorage(tmp_path / "t.json").oauth_metadata() == _metadata()


async def test_login_saves_the_discovered_metadata(tmp_path: Path):
    from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata

    from fleetwatch.epiphan.auth import make_provider

    s = FileTokenStorage(tmp_path / "t.json")
    provider = make_provider(URL, s, 8765, interactive=False)
    m = _metadata()
    provider.context.oauth_metadata = OAuthMetadata.model_validate(m["metadata"])
    provider.context.protected_resource_metadata = ProtectedResourceMetadata.model_validate(m["protected_resource"])
    provider.context.auth_server_url = AS
    body = {"access_token": "FAKENEW", "token_type": "Bearer", "expires_in": 3600, "refresh_token": "FAKER"}
    await provider._handle_token_response(httpx2.Response(200, json=body, request=httpx2.Request("POST", TOKEN_URL)))
    saved = s.oauth_metadata()
    assert saved["metadata"]["token_endpoint"] == TOKEN_URL and saved["auth_server_url"] == AS
    assert saved["protected_resource"]["resource"].rstrip("/") == URL
    assert "FAKENEW" not in json.dumps(saved), "metadata only, never the token"


async def test_refresh_after_restart_uses_the_stored_token_endpoint(tmp_path: Path):
    from fleetwatch.epiphan.auth import make_provider

    provider = make_provider(URL, await _expired_store(tmp_path, _metadata()), 8765, interactive=False)
    flow = provider._auth_flow(httpx2.Request("POST", URL))
    first = await flow.__anext__()
    assert str(first.url) == TOKEN_URL, "the real endpoint, not a guess"
    await flow.aclose()


async def test_two_processes_refreshing_at_once_rotate_the_token_once(tmp_path: Path):
    """`run` and a cron `digest` wake with the same expired token. Epiphan rotates the refresh token, so only one
    of them may send it: the other waits, re-reads the store and uses the token the first one saved."""
    from fleetwatch.epiphan.auth import make_provider

    await _expired_store(tmp_path, _metadata())
    a = make_provider(URL, FileTokenStorage(tmp_path / "t.json"), 8765, interactive=False)
    b = make_provider(URL, FileTokenStorage(tmp_path / "t.json"), 8765, interactive=False)  # the other process
    flow_a, flow_b = a._auth_flow(httpx2.Request("POST", URL)), b._auth_flow(httpx2.Request("POST", URL))
    refresh_a = await flow_a.__anext__()
    assert str(refresh_a.url) == TOKEN_URL and b"FAKEREFRESH" in refresh_a.content

    async def run_b() -> httpx2.Request:  # one task drives B's whole flow, as httpx would
        first = await flow_b.__anext__()
        await flow_b.aclose()
        return first

    task_b = asyncio.create_task(run_b())  # B wakes while A's refresh is in flight
    await asyncio.sleep(0.5)
    assert not task_b.done(), "B waits for A's refresh instead of sending the same refresh token"

    body = {"access_token": "FAKENEW", "token_type": "Bearer", "expires_in": 3600, "refresh_token": "FAKER2"}
    main_a = await flow_a.asend(httpx2.Response(200, json=body, request=refresh_a))
    assert str(main_a.url) == URL and main_a.headers["Authorization"] == "Bearer FAKENEW"

    main_b = await asyncio.wait_for(task_b, 5)
    assert str(main_b.url) == URL, "no second refresh went out"
    assert main_b.headers["Authorization"] == "Bearer FAKENEW", "B uses the token A saved"
    assert json.loads((tmp_path / "t.json").read_text())["tokens"]["refresh_token"] == "FAKER2"
    assert not a.dead and not b.dead
    await flow_a.aclose()


async def test_refresh_lock_is_released_after_the_refresh(tmp_path: Path):
    """The lock covers the refresh only, not the request after it: a slow read must not hold up another process."""
    from fleetwatch.epiphan.auth import make_provider

    await _expired_store(tmp_path, _metadata())
    a = make_provider(URL, FileTokenStorage(tmp_path / "t.json"), 8765, interactive=False)
    flow_a = a._auth_flow(httpx2.Request("POST", URL))
    refresh_a = await flow_a.__anext__()
    body = {"access_token": "FAKENEW", "token_type": "Bearer", "expires_in": 3600, "refresh_token": "FAKER2"}
    await flow_a.asend(httpx2.Response(200, json=body, request=refresh_a))  # A's main request is now in flight
    other = FileTokenStorage(tmp_path / "t.json").refresh_lock()
    assert await other.acquire(timeout_s=1), "free as soon as the new token is saved"
    other.release()
    await flow_a.aclose()


async def test_no_stored_metadata_falls_back_to_the_guess_with_a_warning(tmp_path: Path, caplog):
    from fleetwatch.epiphan.auth import make_provider

    provider = make_provider(URL, await _expired_store(tmp_path, None), 8765, interactive=False)
    with caplog.at_level(logging.WARNING):
        flow = provider._auth_flow(httpx2.Request("POST", URL))
        first = await flow.__anext__()
    assert str(first.url) == "https://mcp.example.invalid/token"
    assert any("fleetwatch login" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    await flow.aclose()


# --- 8. a refused refresh means "sign in again", not a restart loop ------------------------------------------
@pytest.mark.parametrize("status", [400, 401])
async def test_refused_refresh_marks_the_sign_in_dead(tmp_path: Path, status):
    from fleetwatch.epiphan.auth import make_provider

    store = await _expired_store(tmp_path, _metadata())
    provider = make_provider(URL, store, 8765, interactive=False)
    flow = provider._auth_flow(httpx2.Request("POST", URL))
    refresh = await flow.__anext__()
    after = await flow.asend(httpx2.Response(status, json={"error": "invalid_grant"}, request=refresh))
    assert str(after.url) == URL
    await flow.aclose()
    assert provider.dead and store.is_dead()
    assert FileTokenStorage(tmp_path / "t.json").is_dead(), "remembered across a restart"


async def test_a_server_error_on_refresh_is_not_dead(tmp_path: Path):
    from fleetwatch.epiphan.auth import make_provider

    store = await _expired_store(tmp_path, _metadata())
    provider = make_provider(URL, store, 8765, interactive=False)
    flow = provider._auth_flow(httpx2.Request("POST", URL))
    refresh = await flow.__anext__()
    await flow.asend(httpx2.Response(503, request=refresh))
    await flow.aclose()
    assert not provider.dead and not store.is_dead()


async def test_a_new_sign_in_clears_the_dead_mark(tmp_path: Path):
    s = FileTokenStorage(tmp_path / "t.json")
    s.mark_dead()
    assert s.is_dead()
    await s.set_tokens(OAuthToken(access_token="FAKENEW", refresh_token="FAKER", expires_in=3600))
    assert not s.is_dead()


async def test_in_band_401_with_a_dead_sign_in_raises_without_retry(tmp_path: Path):
    from fleetwatch.epiphan.mcp import SignInDead

    c = await _signed_in_client(tmp_path)
    c._provider.dead = True
    c._client = _FakeSDK([UNAUTHORIZED])
    with pytest.raises(SignInDead):
        await c.call("get_devices_in_my_team")
    assert c._client.calls == 1


async def test_dead_sign_in_exits_78_at_once(caplog):
    from fleetwatch.epiphan.mcp import SignInDead
    from fleetwatch.runner import run_loop

    class Dead(_ScriptedClient):
        async def call(self, tool, arguments=None):
            raise SignInDead("refresh refused")

    async def no_sleep(_):
        raise AssertionError("must exit on the first beat")

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as e:
        await run_loop(lambda: Dead([], []), State(), POLICY, Capture(), first_run=True, sleep=no_sleep)
    assert e.value.code == 78
    assert any("Sign-in expired: run fleetwatch login" in r.getMessage() for r in caplog.records)


async def test_run_with_a_dead_store_exits_78_before_calling_epiphan(tmp_path: Path, monkeypatch, caplog):
    from fleetwatch import cli
    from fleetwatch.config import Settings

    FileTokenStorage(tmp_path / "t.json").mark_dead()

    def no_client(settings):
        raise AssertionError("must not connect")

    monkeypatch.setattr(cli, "_make_client", no_client)
    s = Settings(
        _env_file=None,
        policy_file=ROOT / "policy.yaml",
        tool_policy_file=ROOT / "tool_policy.yaml",
        state_db=tmp_path / "state.db",
        token_file=tmp_path / "t.json",
        token_store="file",
        epiphan_token=None,
        slack_app_token=None,
    )
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as e:
        await cli._run(s)
    assert e.value.code == 78
    assert any("Sign-in expired: run fleetwatch login" in r.getMessage() for r in caplog.records)


def test_doctor_says_sign_in_again_when_the_token_is_dead(tmp_path: Path):
    from fleetwatch.doctor import run_checks
    from tests.test_doctor import by_name, settings

    f = tmp_path / "epiphan-oauth.json"
    f.write_text(json.dumps({"tokens": {"access_token": "FAKESECRET"}, "dead": True}))
    f.chmod(0o600)
    row = by_name(run_checks(settings(tmp_path), reach=lambda u: True, service=lambda: ("OK", "running")))[
        "Token expiry"
    ]
    assert row.status == "FAIL" and "sign in again" in row.detail and "fleetwatch login" in row.detail


def test_systemd_does_not_restart_a_dead_sign_in():
    unit = (ROOT / "deploy/fleetwatch.service").read_text()
    assert "Restart=on-failure" in unit and "RestartPreventExitStatus=78" in unit
