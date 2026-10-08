import json
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


@pytest.fixture(autouse=True)
def _no_anthropic(monkeypatch):
    """No test reaches Anthropic: the key is empty whatever the shell or a .env says, and building a real client
    fails. A test that needs the assistant passes a scripted fake as `client=`."""
    monkeypatch.setenv("FLEETWATCH_ANTHROPIC_API_KEY", "")

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to build a real Anthropic client")

    monkeypatch.setattr("fleetwatch.assistant.make_client", refuse, raising=False)
