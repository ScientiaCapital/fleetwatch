from pathlib import Path

import pytest

from fleetwatch.epiphan.approval_read import read_for_approval
from fleetwatch.epiphan.replay import ReplayClient
from fleetwatch.heartbeat import FailedRead
from fleetwatch.policy import load_tools
from tests.conftest import FIXTURES, NOW


class Flaky(ReplayClient):
    """A replay client where chosen tools fail, or answer with something else."""

    def __init__(self, directory: Path, *, fail: set[str] = frozenset(), answers: dict | None = None):
        super().__init__(directory, load_tools(), NOW)
        self.fail, self.answers = fail, answers or {}

    async def call(self, tool, arguments=None):
        if tool in self.fail:
            raise RuntimeError("Epiphan said no, token=abc123")
        if tool in self.answers:
            return self.answers[tool]
        return await super().call(tool, arguments)


async def test_reads_the_fleet_and_names_the_destinations():
    fleet = await read_for_approval(ReplayClient(FIXTURES, load_tools(), NOW), NOW)
    assert fleet.devices
    assert fleet.endpoints is not None
    [endpoint] = fleet.endpoints.values()
    assert endpoint.name == "Rehearsal stream"
    assert endpoint.host == "rehearsal.example.invalid"


async def test_failed_endpoints_read_leaves_none_not_empty(caplog):
    fleet = await read_for_approval(Flaky(FIXTURES, fail={"get_stream_endpoints"}), NOW)
    assert fleet.devices
    assert fleet.endpoints is None  # a Deny-only card, never "no destinations"
    assert "abc123" not in caplog.text  # the failure is logged redacted


@pytest.mark.parametrize("answer", ["not json at all", {"unexpected": 1}])
async def test_unknown_endpoints_shape_leaves_none(answer):
    fleet = await read_for_approval(Flaky(FIXTURES, answers={"get_stream_endpoints": answer}), NOW)
    assert fleet.endpoints is None


async def test_empty_endpoint_list_is_not_a_failed_read():
    fleet = await read_for_approval(Flaky(FIXTURES, answers={"get_stream_endpoints": {"stream_endpoints": []}}), NOW)
    assert fleet.endpoints == {}


async def test_a_failed_fleet_read_is_not_swallowed():
    with pytest.raises(RuntimeError):
        await read_for_approval(Flaky(FIXTURES, fail={"get_devices_in_my_team"}), NOW)


async def test_a_fleet_read_in_an_unknown_shape_raises_failed_read():
    with pytest.raises(FailedRead):
        await read_for_approval(Flaky(FIXTURES, answers={"get_devices_in_my_team": "text"}), NOW)


async def test_strict_makes_a_failed_recorder_read_fail_the_card():
    with pytest.raises(FailedRead):
        await read_for_approval(Flaky(FIXTURES, fail={"get_recorder_status_for_devices"}), NOW, strict=True)
    fleet = await read_for_approval(Flaky(FIXTURES, fail={"get_recorder_status_for_devices"}), NOW)
    assert fleet.devices  # not strict: the card is still built
