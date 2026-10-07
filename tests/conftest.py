import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from proav_agent.epiphan.parse import parse_devices
from proav_agent.policy import Policy

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
