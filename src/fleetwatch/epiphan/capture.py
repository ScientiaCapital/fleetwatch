"""Save every tool result a heartbeat reads as replay files, so a second sample can be built from a real fleet
without signing in again. Wraps any client (the real one or the replay one) and writes what `call` returned.

The wrapper does not redact: the inner client already returns redacted results (EpiphanClient.call and
ReplayClient.call both pass through `redact()`), so what lands on disk is what the heartbeat saw. The file name is
the tool name, checked against the read list first: never anything from a payload. A capture is still real fleet
data; the folder is private (0700/0600) and the manifest says it is not for commit."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fleetwatch.epiphan.token_store import _create_private, _private_dir

MANIFEST = "_manifest.json"  # can't collide with a tool: read tools start with get_ or kb_
_NAME = re.compile(r"[a-z][a-z0-9_]*")
NOTE = (
    "Captured by Fleetwatch. Redacted tool results from a real fleet: not for commit. "
    "Rename rooms and remove IDs, IPs and serials before sharing."
)


class CapturingClient:
    def __init__(self, inner, directory: Path):
        self.inner, self.directory = inner, Path(directory)
        self.captured: set[str] = set()

    @property
    def tools(self):
        return self.inner.tools

    def guard(self, tool: str) -> None:
        self.inner.guard(tool)

    async def __aenter__(self):
        await self.inner.__aenter__()
        _private_dir(self.directory)
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            await self.inner.__aexit__(*exc)
        finally:
            now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._write(MANIFEST, {"note": NOTE, "captured_at": now, "tools": sorted(self.captured)})

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        from fleetwatch.epiphan.mcp import ToolNotAllowed

        if not (self.tools.is_read(tool) and _NAME.fullmatch(tool)):
            raise ToolNotAllowed(f"refused to capture {tool!r}: not on the read list")
        result = await self.inner.call(tool, arguments)
        self._write(f"{tool}.json", result)
        self.captured.add(tool)
        return result

    def _write(self, name: str, value: Any) -> None:
        with open(_create_private(self.directory / name), "w", encoding="utf-8") as f:
            f.write(json.dumps(value, indent=2) + "\n")
