"""The service files: what launchd and systemd start must behave like the terminal.

The console notifier prints digests with `print`. When stdout is a file or a pipe, Python buffers it in blocks, so
a digest might not reach the log for hours and is lost on a hard crash. Both service files set PYTHONUNBUFFERED,
like the Dockerfile does.
"""

import plistlib
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def test_launchd_agent_prints_unbuffered() -> None:
    plist = plistlib.loads((DEPLOY / "dev.fleetwatch.agent.plist").read_bytes())
    assert plist["EnvironmentVariables"]["PYTHONUNBUFFERED"] == "1"
    assert plist["ProgramArguments"][-2:] == ["fleetwatch", "run"]


def test_systemd_unit_prints_unbuffered() -> None:
    unit = (DEPLOY / "fleetwatch.service").read_text().splitlines()
    assert "Environment=PYTHONUNBUFFERED=1" in unit
    assert any(line.startswith("ExecStart=") and line.endswith("fleetwatch run") for line in unit)
