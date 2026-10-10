"""`fleetwatch connect`: the guided first run. Region, sign-in, a check that the team shows devices.

Offline. The real sign-in and the Epiphan read are replaced below; nothing here opens a browser or calls Epiphan.
"""

import asyncio
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleetwatch import cli, connect
from fleetwatch.config import Settings

NA, EU, AU = connect.REGIONS["na"][1], connect.REGIONS["eu"][1], connect.REGIONS["au"][1]


def _answers(*typed):
    it = iter(typed)

    def ask(_prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    return ask


# --- the region menu ---------------------------------------------------------------------------------------
def test_region_of_knows_the_three_servers_and_nothing_else():
    assert connect.region_of(NA) == "na" and connect.region_of(EU) == "eu" and connect.region_of(AU) == "au"
    assert connect.region_of(EU + "/") == "eu", "a trailing slash is the same server"
    assert connect.region_of("https://edge.example.invalid/mcp") is None


@pytest.mark.parametrize(
    ("typed", "current", "want"),
    [
        pytest.param([""], NA, NA, id="enter keeps North America"),
        pytest.param([""], EU, EU, id="enter keeps the current region"),
        pytest.param(["2"], NA, EU, id="2 is Europe"),
        pytest.param(["3"], NA, AU, id="3 is Australia"),
        pytest.param(["1"], EU, NA, id="1 is North America"),
        pytest.param(["x", "9", "3"], NA, AU, id="a wrong answer asks again"),
        pytest.param([], EU, EU, id="no keyboard keeps the current region"),
        pytest.param([""], "https://edge.example.invalid/mcp", "https://edge.example.invalid/mcp", id="custom kept"),
    ],
)
def test_choose_region(typed, current, want):
    assert connect.choose_region(current, _answers(*typed), lambda *_: None) == want


# --- saving the choice in .env -----------------------------------------------------------------------------
def test_set_env_url_replaces_the_line_and_leaves_everything_else(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(f"# note\nFLEETWATCH_EPIPHAN_MCP_URL={NA}\nFLEETWATCH_SLACK_CHANNEL=#av-ops\n")
    env.chmod(0o600)
    connect.set_env_url(env, EU)
    assert env.read_text() == f"# note\nFLEETWATCH_EPIPHAN_MCP_URL={EU}\nFLEETWATCH_SLACK_CHANNEL=#av-ops\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_set_env_url_appends_when_the_line_is_missing_and_creates_a_private_file(tmp_path: Path):
    env = tmp_path / ".env"
    connect.set_env_url(env, AU)
    assert env.read_text() == f"FLEETWATCH_EPIPHAN_MCP_URL={AU}\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    connect.set_env_url(env, EU)
    assert env.read_text().count("FLEETWATCH_EPIPHAN_MCP_URL") == 1, "no second line"


def test_set_env_url_leaves_a_commented_example_alone(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(f"# FLEETWATCH_EPIPHAN_MCP_URL={NA}\nFLEETWATCH_SLACK_CHANNEL=#av-ops\n")
    connect.set_env_url(env, EU)
    assert env.read_text().startswith(f"# FLEETWATCH_EPIPHAN_MCP_URL={NA}\n")
    assert f"\nFLEETWATCH_EPIPHAN_MCP_URL={EU}\n" in env.read_text()


# --- the whole command ---------------------------------------------------------------------------------------
@pytest.fixture
def world(monkeypatch, tmp_path: Path):
    """No real sign-in or network. `w.signed_in`, `w.devices`, `w.fail_login` steer it; `w.calls` records it."""

    w = SimpleNamespace(signed_in=False, devices=12, fail_login=None, read_works=True, calls=[])

    async def login(settings, sandbox=False):
        w.calls.append(("login", settings.epiphan_mcp_url))
        if w.fail_login:
            raise w.fail_login
        w.signed_in = True
        return w.devices

    async def read_count(settings):
        w.calls.append(("read", settings.epiphan_mcp_url))
        if not w.read_works:
            raise RuntimeError("Sign-in expired")
        return w.devices

    def logout(settings, sandbox=False):
        w.calls.append(("logout", settings.epiphan_mcp_url))
        w.signed_in = False
        return "Signed out on this machine."

    monkeypatch.setattr(cli, "_login", login)
    monkeypatch.setattr(cli, "_read_count", read_count)
    monkeypatch.setattr(cli, "_logout", logout)
    monkeypatch.setattr(cli, "_signed_in", lambda settings: w.signed_in)
    w.env = tmp_path / ".env"
    w.env.write_text(f"FLEETWATCH_EPIPHAN_MCP_URL={NA}\n")
    w.settings = Settings(_env_file=None, epiphan_mcp_url=NA)
    return w


def _connect(w, *typed, region=None):
    asyncio.run(cli._connect(w.settings, region, ask=_answers(*typed), env_path=w.env))


def test_first_run_picks_a_region_signs_in_and_says_what_to_do_next(world, capsys):
    _connect(world, "2")
    out = capsys.readouterr().out
    assert world.calls == [("login", EU)], "signed in against the region just chosen"
    assert f"FLEETWATCH_EPIPHAN_MCP_URL={EU}" in world.env.read_text()
    assert "Connected. Your team has 12 devices." in out
    assert "fleetwatch digest" in out and "fleetwatch doctor" in out
    assert "least access" in out, "the account advice is on the screen, not only in the docs"


def test_region_flag_skips_the_menu(world, capsys):
    _connect(world, region="au")  # no answers given: asking would hit EOF and keep NA, so a login on AU proves it
    assert world.calls == [("login", AU)]
    assert "Where is your Epiphan Edge account" not in capsys.readouterr().out


def test_zero_devices_points_at_the_team_and_the_region(world, capsys):
    world.devices = 0
    _connect(world, "")
    out = capsys.readouterr().out
    assert "no devices" in out.lower() and "team" in out and "region" in out
    assert "fleetwatch connect" in out, "and how to try again"
    assert "Connected." not in out


def test_a_sign_in_that_does_not_finish_is_plain_words_and_exit_1(world, capsys):
    world.fail_login = RuntimeError("callback timed out; Bearer FAKESECRET99")
    with pytest.raises(SystemExit) as e:
        _connect(world, "")
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "did not finish" in out and "fleetwatch connect" in out
    assert "FAKESECRET99" not in out, "whatever the error carries goes through redact()"
    assert "Traceback" not in out


def test_already_signed_in_checks_the_sign_in_and_does_not_open_a_browser(world, capsys):
    world.signed_in = True
    _connect(world, "")
    assert world.calls == [("read", NA)]
    assert "already signed in" in capsys.readouterr().out.lower()


def test_a_saved_sign_in_that_no_longer_works_signs_in_again(world, capsys):
    world.signed_in, world.read_works = True, False
    _connect(world, "")
    assert [c[0] for c in world.calls] == ["read", "login"]
    assert "no longer works" in capsys.readouterr().out


def test_switching_region_forgets_the_old_sign_in_first(world):
    world.signed_in = True
    _connect(world, "3")
    assert [c[0] for c in world.calls] == ["logout", "login"]
    assert world.calls[0] == ("logout", NA), "signed out of the server it was signed in to"
    assert world.calls[1] == ("login", AU)


def test_connect_never_reaches_for_the_sandbox_slot():
    import inspect

    assert "sandbox" not in inspect.getsource(cli._connect).lower(), "the sandbox sign-in has its own command"
