import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fleetwatch.epiphan.parse import parse_devices
from fleetwatch.policy import Policy

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 7, 15, 0, tzinfo=UTC)


@pytest.fixture
def device_list():
    return json.loads((FIXTURES / "get_devices_in_my_team.json").read_text())


@pytest.fixture
def fleet(device_list):
    return parse_devices(device_list, NOW)


@pytest.fixture
def policy():
    return Policy()


_REAL_SUBPROCESS_RUN = subprocess.run


@pytest.fixture(autouse=True)
def _no_real_sign_in(monkeypatch, tmp_path_factory):
    """No test reaches the developer's own sign-in (#163). Settings point at an empty file store in a temporary
    directory, whatever the shell or a .env says, and a Keychain or systemd-creds call fails the test unless the
    test has faked `subprocess.run` (tests/test_token_store.py does)."""
    from fleetwatch.epiphan import token_store as ts

    d = tmp_path_factory.mktemp("sign-in")
    monkeypatch.setenv("FLEETWATCH_TOKEN_STORE", "file")
    monkeypatch.setenv("FLEETWATCH_TOKEN_FILE", str(d / "epiphan-oauth.json"))
    monkeypatch.setenv("FLEETWATCH_SANDBOX_TOKEN_FILE", str(d / "epiphan-sandbox-oauth.json"))
    real = ts._run

    def guarded(argv, stdin=None):
        if argv[0] in ("security", "systemd-creds") and ts.subprocess.run is _REAL_SUBPROCESS_RUN:
            raise AssertionError(f"a test tried to run the real {argv[0]}; fake subprocess.run instead")
        return real(argv, stdin)

    monkeypatch.setattr(ts, "_run", guarded)


@pytest.fixture(autouse=True)
def _no_anthropic(monkeypatch):
    """No test reaches Anthropic: the key is empty whatever the shell or a .env says, and building a real client
    fails. A test that needs the assistant passes a scripted fake as `client=`."""
    monkeypatch.setenv("FLEETWATCH_ANTHROPIC_API_KEY", "")

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to build a real Anthropic client")

    monkeypatch.setattr("fleetwatch.assistant.make_client", refuse, raising=False)
