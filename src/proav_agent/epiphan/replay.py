"""Replay saved tool results instead of calling Epiphan. For development, demos and tests: the heartbeat
runs end to end with no sign-in. Same guard as the real client: only read tools, and only files that exist."""

import json
from pathlib import Path
from typing import Any

from proav_agent.policy import ToolPolicy
from proav_agent.redact import redact


class ReplayClient:
    def __init__(self, directory: Path, tools: ToolPolicy):
        self.directory, self.tools = Path(directory), tools

    def guard(self, tool: str) -> None:
        from proav_agent.epiphan.mcp import ToolNotAllowed

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
        return redact(json.loads(path.read_text()))
