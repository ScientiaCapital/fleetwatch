"""Replay saved tool results instead of calling Epiphan. For development, demos and tests: the heartbeat
runs end to end with no sign-in. Same guard as the real client: only read tools, and only files that exist.

Saved results may contain relative times such as `"{{now+25m}}"` or `"{{now-2h}}"`, resolved against the replay's
clock, so a sample with a class "starting in 25 minutes" stays true whenever the demo runs."""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fleetwatch.policy import ToolPolicy
from fleetwatch.redact import redact

_RELATIVE = re.compile(r"\{\{now(?:([+-])(\d+)([mh]))?\}\}")


def resolve_relative_times(text: str, now: datetime) -> str:
    def iso(m: re.Match) -> str:
        delta = timedelta(0)
        if m.group(1):
            n = int(m.group(2))
            delta = timedelta(minutes=n) if m.group(3) == "m" else timedelta(hours=n)
            delta = -delta if m.group(1) == "-" else delta
        return (now + delta).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    return _RELATIVE.sub(iso, text)


class ReplayClient:
    def __init__(self, directory: Path, tools: ToolPolicy, now: datetime | None = None):
        self.directory, self.tools = Path(directory), tools
        self.now = now or datetime.now(UTC)  # fixed for the client's life, so repeat ticks see the same sample

    def guard(self, tool: str) -> None:
        from fleetwatch.epiphan.mcp import ToolNotAllowed

        if not self.tools.is_read(tool):
            raise ToolNotAllowed(f"refused non-read tool {tool!r} in replay")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        self.guard(tool)
        path = self.directory / f"{tool}.json"
        if not path.exists():
            raise FileNotFoundError(f"no saved result for {tool} in {self.directory}")
        return redact(json.loads(resolve_relative_times(path.read_text(), self.now)))
