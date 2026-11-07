"""Epiphan's own tool schemas don't always match its data (a live read returned `null` where the schema said object).
The SDK checks every result against the tool's declared output schema and raises, so the whole read failed before
Fleetwatch saw any data. Fleetwatch already treats results as untrusted and parses them defensively, so a mismatch
is logged and the result is read anyway. Nothing here signs in or calls Epiphan.
"""

import logging

import pytest

from fleetwatch.epiphan import mcp
from fleetwatch.epiphan.mcp import tolerate_schema_mismatch


@pytest.fixture(autouse=True)
def _fresh_process():
    mcp.reset_schema_warnings()
    yield
    mcp.reset_schema_warnings()


class FakeSession:
    def __init__(self, error: Exception | None):
        self.error, self.calls = error, []

    async def validate_tool_result(self, name, result):
        self.calls.append(name)
        if self.error:
            raise self.error


async def test_a_result_that_breaks_the_declared_schema_is_read_anyway_and_the_log_never_carries_the_data(caplog):
    secret = "rtmp://example.invalid/live/SECRETKEY123"
    s = FakeSession(
        RuntimeError(f"Invalid structured content returned by tool get_x: {secret} is not of type 'object'")
    )
    tolerate_schema_mismatch(s)
    with caplog.at_level(logging.WARNING):
        await s.validate_tool_result("get_x", object())  # does not raise
    assert s.calls == ["get_x"]
    assert "get_x" in caplog.text and "SECRETKEY123" not in caplog.text and "example.invalid" not in caplog.text


async def test_a_missing_structured_result_is_also_tolerated():
    s = FakeSession(RuntimeError("Tool get_x has an output schema but did not return structured content"))
    tolerate_schema_mismatch(s)
    await s.validate_tool_result("get_x", object())


@pytest.mark.parametrize(
    "error",
    [RuntimeError("Invalid schema for tool get_x: bad $ref"), ValueError("boom"), ConnectionError("down")],
)
async def test_anything_else_still_raises(error):
    s = FakeSession(error)
    tolerate_schema_mismatch(s)
    with pytest.raises(type(error)):
        await s.validate_tool_result("get_x", object())


async def test_a_conforming_result_passes_through_untouched():
    s = FakeSession(None)
    tolerate_schema_mismatch(s)
    await s.validate_tool_result("get_x", object())
    assert s.calls == ["get_x"]


async def test_the_mismatch_is_a_warning_once_per_tool_then_debug(caplog):
    """#127: the live soak logged the same mismatch on every heartbeat, about 480 WARNING lines a day."""
    mismatch = RuntimeError("Invalid structured content returned by tool get_x: None is not of type 'object'")
    caplog.set_level(logging.DEBUG, logger="fleetwatch.epiphan.mcp")
    for _ in range(3):  # three heartbeats, each on a fresh session after a failed beat
        s = FakeSession(mismatch)
        tolerate_schema_mismatch(s)
        await s.validate_tool_result("get_x", object())
    s = FakeSession(mismatch)
    tolerate_schema_mismatch(s)
    await s.validate_tool_result("get_y", object())
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len([w for w in warnings if "get_x" in w]) == 1 and len([w for w in warnings if "get_y" in w]) == 1
    assert len([r for r in caplog.records if r.levelno == logging.DEBUG and "get_x" in r.getMessage()]) == 2
    assert mcp.schema_mismatches() == ["get_x", "get_y"]
