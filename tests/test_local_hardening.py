"""Local-machine hardening: private token files, redacted logs, a strict login callback, secret settings,
a packaged read list that can only narrow, and the small HTTP edges. Nothing here signs in or calls Epiphan."""

import asyncio
import http.client
import io
import json
import logging
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from mcp.shared.auth import OAuthToken
from pydantic import ValidationError

from fleetwatch.config import Settings
from fleetwatch.epiphan import token_store as ts
from fleetwatch.epiphan.auth import _Redirect, make_provider, parse_callback
from fleetwatch.epiphan.token_store import FileTokenStorage, SystemdCredsTokenStorage
from fleetwatch.policy import KNOWN_WRITE_TOOLS, load_tool_policy, load_tools

ROOT = Path(__file__).resolve().parents[1]
FAKE_URL = "rtmp://a.example/live/FAKEKEY"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- M1: the token file and its tmp file are never wider than 0600 ---------------------------------------------
@pytest.fixture
def umask_022():
    old = os.umask(0o022)
    yield
    os.umask(old)


@pytest.fixture
def modes(monkeypatch):
    """Every mode passed to os.open (with O_CREAT) or os.chmod, by path."""
    seen: list[tuple[str, str, int]] = []
    real_open, real_chmod = os.open, os.chmod

    def rec_open(path, flags, mode=0o777, *a, **k):
        if flags & os.O_CREAT:
            seen.append(("open", str(path), mode))
        return real_open(path, flags, mode, *a, **k)

    def rec_chmod(path, mode, *a, **k):
        seen.append(("chmod", str(path), mode))
        return real_chmod(path, mode, *a, **k)

    monkeypatch.setattr(os, "open", rec_open)
    monkeypatch.setattr(os, "chmod", rec_chmod)
    return seen


async def test_file_token_is_written_0600_from_the_start(tmp_path, umask_022, modes):
    folder = tmp_path / "dot-fleetwatch"
    s = FileTokenStorage(folder / "epiphan-oauth.json")
    await s.set_tokens(OAuthToken(access_token="FAKEACCESS"))

    tmp_opens = [m for kind, p, m in modes if kind == "open" and p.endswith(".tmp")]
    assert tmp_opens, "the tmp file must be created with os.open and an explicit mode"
    assert all(m & 0o077 == 0 for _, _, m in modes), f"something was created or chmod-ed wider than 0600: {modes}"
    assert stat.S_IMODE((folder / "epiphan-oauth.json").stat().st_mode) == 0o600
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert not (folder / "epiphan-oauth.tmp").exists()


async def test_an_existing_folder_is_left_alone(tmp_path, umask_022):
    folder = tmp_path / "shared"
    folder.mkdir(mode=0o750)
    os.chmod(folder, 0o750)
    await FileTokenStorage(folder / "t.json").set_tokens(OAuthToken(access_token="FAKEACCESS"))
    assert stat.S_IMODE(folder.stat().st_mode) == 0o750, "only a folder we created is tightened"
    assert stat.S_IMODE((folder / "t.json").stat().st_mode) == 0o600


async def test_a_symlink_at_the_tmp_path_is_not_followed(tmp_path, umask_022):
    target = tmp_path / "elsewhere.txt"
    target.write_text("untouched")
    (tmp_path / "t.tmp").symlink_to(target)
    await FileTokenStorage(tmp_path / "t.json").set_tokens(OAuthToken(access_token="FAKEACCESS"))
    assert target.read_text() == "untouched"
    assert json.loads((tmp_path / "t.json").read_text())["tokens"]["access_token"] == "FAKEACCESS"


async def test_systemd_creds_tmp_is_never_wider_than_0600(tmp_path, umask_022, monkeypatch):
    seen_modes: list[int] = []

    def fake_run(argv, stdin=None):
        assert argv[0] == "systemd-creds" and argv[1] == "encrypt"
        dst = argv[-1]
        with open(dst, "w") as f:  # like the real tool: writes with whatever umask the process has
            f.write("ENCRYPTED")
        seen_modes.append(stat.S_IMODE(os.stat(dst).st_mode))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(ts, "_run", fake_run)
    s = SystemdCredsTokenStorage(tmp_path / "fw" / "t.cred")
    s._save({"tokens": {"access_token": "FAKEACCESS"}})
    assert seen_modes == [0o600], f"the encrypted tmp file was {oct(seen_modes[0])} while systemd-creds wrote it"
    assert stat.S_IMODE((tmp_path / "fw" / "t.cred").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "fw").stat().st_mode) == 0o700
    assert os.umask(0o022) == 0o022, "the process umask is restored"


# --- H1: logs are redacted, and the SDK's raw SSE logging stays off under -v -----------------------------------
@pytest.fixture
def clean_logging():
    root = logging.getLogger()
    names = ("mcp", "httpx", "httpx2", "httpcore", "httpcore2")
    saved = (root.level, list(root.handlers), {n: logging.getLogger(n).level for n in names})
    yield
    root.setLevel(saved[0])
    root.handlers[:] = saved[1]
    for n, level in saved[2].items():
        logging.getLogger(n).setLevel(level)


def test_debug_records_are_redacted_and_noisy_loggers_stay_quiet(clean_logging):
    from fleetwatch.logsetup import configure_logging

    out = io.StringIO()
    logging.getLogger().addHandler(logging.StreamHandler(out))
    configure_logging(verbose=True)

    logging.getLogger("fleetwatch.test").debug("pushing to %s", FAKE_URL)
    logging.getLogger("fleetwatch.test").debug(f"SSE message: {{'url': '{FAKE_URL}'}}")
    try:
        raise RuntimeError(f"connect failed for {FAKE_URL}")
    except RuntimeError:
        logging.getLogger("fleetwatch.test").exception("boom")
    text = out.getvalue()
    assert "pushing to" in text and "[redacted]" in text
    assert "FAKEKEY" not in text

    assert logging.getLogger().level == logging.DEBUG
    for name in ("mcp", "mcp.client.streamable_http", "httpx", "httpx2", "httpcore", "httpcore2.http11"):
        assert logging.getLogger(name).getEffectiveLevel() == logging.WARNING, name


def test_cli_verbose_keeps_mcp_and_httpx2_at_warning(clean_logging, monkeypatch, capsys):
    from fleetwatch import cli

    monkeypatch.setattr(sys, "argv", ["fleetwatch", "-v", "digest", "--replay", "tests/fixtures"])
    cli.main()
    assert logging.getLogger().level == logging.DEBUG
    assert logging.getLogger("mcp").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("httpx2").getEffectiveLevel() == logging.WARNING


def test_doctor_quiets_the_http_library_mcp_really_uses(clean_logging, tmp_path):
    from tests.test_doctor import run, settings

    logging.getLogger("httpx2").setLevel(logging.NOTSET)
    run(settings(tmp_path))
    assert logging.getLogger("httpx2").level == logging.WARNING


# --- L4: exception text is redacted before it is logged ---------------------------------------------------------
def test_slack_post_failure_is_logged_redacted(caplog):
    from fleetwatch.notify.slack import SlackNotifier

    class Boom:
        def chat_postMessage(self, **kw):
            raise RuntimeError(f"proxy said no to {FAKE_URL}")

    n = SlackNotifier.__new__(SlackNotifier)
    n.channel, n._web = "#av-ops", Boom()
    with caplog.at_level(logging.WARNING):
        assert n.post("hi") is False
    assert "Slack post failed" in caplog.text and "FAKEKEY" not in caplog.text


# --- M2, M3, M4: the login callback -----------------------------------------------------------------------------
def test_redirect_uri_is_the_loopback_ip_with_a_path(tmp_path):
    p = make_provider("https://example.invalid/mcp", FileTokenStorage(tmp_path / "t.json"), 8765, interactive=False)
    (uri,) = p.context.client_metadata.redirect_uris
    assert str(uri) == "http://127.0.0.1:8765/callback"


def test_parse_callback_keeps_iss_and_needs_state():
    got = parse_callback("http://127.0.0.1:8765/callback?code=abc&state=xyz&iss=https%3A%2F%2Fauth.example")
    assert (got.code, got.state, got.iss) == ("abc", "xyz", "https://auth.example")
    assert parse_callback("code=only") is None, "the SDK refuses a missing state anyway; don't end the wait for it"


def _get(port: int, path: str) -> tuple[int, str]:
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    return r.status, r.read().decode()


async def _wait_listening(port: int) -> None:
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            return
        except OSError:
            await asyncio.sleep(0.05)
    raise AssertionError("callback server never started")


async def test_callback_ignores_other_paths_and_finishes_on_callback(tmp_path):
    port = _free_port()
    task = asyncio.create_task(_Redirect(port, prompt=False).wait())
    await _wait_listening(port)

    assert (await asyncio.to_thread(_get, port, "/favicon.ico"))[0] == 404
    assert (await asyncio.to_thread(_get, port, "/?code=abc&state=xyz"))[0] == 404
    status, _ = await asyncio.to_thread(_get, port, "/callback")
    assert status == 400, "a bare /callback is ignored"
    status, _ = await asyncio.to_thread(_get, port, "/callback?code=abc")
    assert status == 400, "code without state is ignored"
    assert not task.done()

    status, body = await asyncio.to_thread(_get, port, "/callback?code=abc&state=xyz&iss=https%3A%2F%2Fauth.example")
    assert status == 200 and "Signed in" in body
    result = await asyncio.wait_for(task, 5)
    assert (result.code, result.state, result.iss) == ("abc", "xyz", "https://auth.example")


async def test_callback_error_shows_a_fixed_message(tmp_path):
    port = _free_port()
    task = asyncio.create_task(_Redirect(port, prompt=False).wait())
    await _wait_listening(port)
    status, body = await asyncio.to_thread(
        _get, port, "/callback?error=access_denied&state=xyz&error_description=Call%20555-0100%20for%20help"
    )
    assert status == 200 and "did not complete" in body and "555-0100" not in body
    with pytest.raises(RuntimeError) as e:
        await asyncio.wait_for(task, 5)
    assert "555-0100" not in str(e.value)


# --- L3: secrets are SecretStr; the MCP URL must be https -------------------------------------------------------
def test_settings_repr_hides_tokens():
    s = Settings(
        _env_file=None,
        slack_bot_token="xoxb-FAKE",
        slack_app_token="xapp-FAKE",
        epiphan_token="FAKE-bearer",
    )
    assert "FAKE" not in repr(s) and "FAKE" not in str(s)
    assert s.slack_bot_token.get_secret_value() == "xoxb-FAKE"


@pytest.mark.parametrize("url", ["http://go.epiphan.cloud/mcp", "go.epiphan.cloud/mcp", "ftp://x/mcp"])
def test_mcp_url_must_be_https(url):
    with pytest.raises(ValidationError, match="https://"):
        Settings(_env_file=None, epiphan_mcp_url=url)


def test_static_token_reaches_the_client_unwrapped(tmp_path):
    from fleetwatch import cli

    s = Settings(
        _env_file=None,
        epiphan_token="FAKE-bearer",
        token_store="file",  # never the real keychain or ~/.fleetwatch from a test
        token_file=tmp_path / "t.json",
        state_db=tmp_path / "state.db",
    )
    client, _, _ = cli._build(s, interactive=False)
    assert client._auth._token == "FAKE-bearer"


# --- L5 and the write-list check: the read list ships in the package and can only narrow -----------------------
def test_tool_policy_ships_inside_the_package():
    from importlib.resources import files

    packaged = files("fleetwatch").joinpath("tool_policy.yaml")
    assert packaged.is_file()
    tp = load_tools()
    assert "get_devices_in_my_team" in tp.read and not (tp.read & KNOWN_WRITE_TOOLS)


def test_tool_policy_does_not_depend_on_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert "get_devices_in_my_team" in load_tools(Settings(_env_file=None).tool_policy_file).read


def test_known_write_tools_is_one_shared_list():
    from fleetwatch import doctor

    assert doctor.KNOWN_WRITE_TOOLS is KNOWN_WRITE_TOOLS
    assert {"batch_reboot", "batch_firmware_update", "create_cms_event"} <= KNOWN_WRITE_TOOLS


def test_a_write_tool_under_read_is_refused_at_load(tmp_path):
    bad = tmp_path / "tool_policy.yaml"
    bad.write_text("read:\n  - get_devices_in_my_team\n  - batch_reboot\nwrite: []\n")
    with pytest.raises(ValueError, match="batch_reboot"):
        load_tool_policy(bad)


def test_a_read_entry_that_is_not_get_or_kb_is_refused(tmp_path):
    bad = tmp_path / "tool_policy.yaml"
    bad.write_text("read:\n  - get_devices_in_my_team\n  - reboot_everything\n")
    with pytest.raises(ValueError, match="reboot_everything"):
        load_tool_policy(bad)


def test_a_user_file_can_narrow_the_read_list(tmp_path):
    mine = tmp_path / "tool_policy.yaml"
    mine.write_text("read:\n  - get_devices_in_my_team\n")
    tp = load_tools(mine)
    assert tp.read == frozenset({"get_devices_in_my_team"})
    assert "batch_reboot" in tp.write, "write and disruptive always come from the packaged file"


def test_a_user_file_cannot_add_a_tool(tmp_path):
    mine = tmp_path / "tool_policy.yaml"
    mine.write_text("read:\n  - get_devices_in_my_team\n  - get_something_new\n")
    with pytest.raises(ValueError, match="get_something_new"):
        load_tools(mine)


def test_root_tool_policy_is_the_packaged_one():
    from importlib.resources import files

    assert (ROOT / "tool_policy.yaml").read_text() == files("fleetwatch").joinpath("tool_policy.yaml").read_text()


# --- L9: the ask page refuses a bad Content-Length ---------------------------------------------------------------
@pytest.mark.parametrize("length", ["-1", "abc", "1.5"])
def test_ask_page_rejects_a_bad_content_length(length):
    import threading
    from http.server import HTTPServer

    from fleetwatch.ask_page import make_handler

    port = _free_port()
    httpd = HTTPServer(("127.0.0.1", port), make_handler(lambda q: "ok", list, port))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.putrequest("POST", "/ask", skip_host=True)
        c.putheader("Host", f"127.0.0.1:{port}")
        c.putheader("Content-Length", length)
        c.endheaders()
        assert c.getresponse().status == 400
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- Slack: the group lookup never retries on its own -----------------------------------------------------------
async def test_group_lookup_client_has_no_retry_handlers(tmp_path, monkeypatch):
    import slack_sdk

    from fleetwatch.policy import Policy
    from fleetwatch.slack_command import start_listener
    from fleetwatch.state import State
    from tests.test_doctor import settings
    from tests.test_slack_command import FakeSocket

    made = []

    class FakeWeb:
        def __init__(self, **kw):
            made.append(kw)

    monkeypatch.setattr(slack_sdk, "WebClient", FakeWeb)
    s = settings(tmp_path, slack_app_token="xapp-1-test", slack_bot_token="xoxb-test")
    p = Policy(quiet_start=None, quiet_end=None, slack_allowed_usergroup="S0GROUP")
    listener = await start_listener(s, p, State(), socket_factory=FakeSocket)
    assert listener is not None
    (kw,) = made
    assert kw["retry_handlers"] == [] and kw["token"] == "xoxb-test"
    listener.close()


# --- v0.2 settings: present, empty by default, and the API key never printed ----------------------------------
def test_v02_settings_defaults(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "AI_MODEL", "WRITE_TEAM_ID", "SANDBOX_TOKEN_FILE"):
        monkeypatch.delenv(f"FLEETWATCH_{name}", raising=False)
    s = Settings(_env_file=None)
    assert s.anthropic_api_key is None
    assert s.ai_model == "claude-haiku-5-5"
    assert s.write_team_id == ""
    assert s.sandbox_token_file.name == "epiphan-sandbox-oauth.json"
    assert s.sandbox_token_file != s.token_file, "the sandbox sign-in has its own slot"


def test_settings_repr_hides_the_anthropic_key():
    s = Settings(_env_file=None, anthropic_api_key="sk-ant-api03-FAKEKEY")
    assert "FAKEKEY" not in repr(s) and "FAKEKEY" not in str(s) and "FAKEKEY" not in str(s.model_dump())
    assert s.anthropic_api_key.get_secret_value() == "sk-ant-api03-FAKEKEY"


def test_env_example_documents_every_v02_setting():
    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text()
    for name in ("ANTHROPIC_API_KEY", "AI_MODEL", "WRITE_TEAM_ID", "SANDBOX_TOKEN_FILE"):
        assert f"FLEETWATCH_{name}" in text
