"""Read-only client for the hosted Epiphan Edge MCP server.

Two safety properties live here, before any request leaves the machine:
1. `guard()` refuses any tool that isn't on the `read` list in tool_policy.yaml, including tools Epiphan adds later.
2. Every result passes through `redact()` before it's parsed, logged or stored.
"""

import json
import logging
from typing import Any, Self

import httpx2 as httpx  # the MCP SDK bundles its own httpx fork; its types must match
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from fleetwatch.epiphan.auth import FileTokenStorage, LoginAuth, make_provider
from fleetwatch.policy import ToolPolicy
from fleetwatch.redact import redact

log = logging.getLogger(__name__)


class ToolNotAllowed(PermissionError):
    pass


class _HttpTransport:
    """Adapts the SDK's streamable HTTP client (with our httpx auth) to the Client's Transport protocol."""

    def __init__(self, url: str, http_client: httpx.AsyncClient):
        self._url, self._http = url, http_client
        self._cm = None

    async def __aenter__(self):
        self._cm = streamable_http_client(self._url, http_client=self._http)
        if hasattr(self._cm, "__aenter__"):
            return await self._cm.__aenter__()
        return await self._cm.__anext__()  # plain async generator

    async def __aexit__(self, *exc):
        try:
            if hasattr(self._cm, "__aexit__"):
                await self._cm.__aexit__(*exc)
            else:
                await self._cm.aclose()
        finally:
            await self._http.aclose()


class EpiphanClient:
    def __init__(
        self,
        url: str,
        tools: ToolPolicy,
        *,
        storage: FileTokenStorage | None = None,
        static_token: str | None = None,
        callback_port: int = 8765,
        interactive: bool = False,
        timeout_s: float = 60,
    ):
        self.url, self.tools = url, tools
        self._timeout = timeout_s
        if static_token:
            self._auth: httpx.Auth | None = _Bearer(static_token)
        elif storage is not None:
            provider = make_provider(url, storage, callback_port, interactive)
            # `login` must start the flow itself: Epiphan reports "401" inside tool results, never as HTTP 401.
            self._auth = LoginAuth(provider, storage) if interactive else provider
        else:
            raise ValueError("either storage or static_token is required")
        self._client: Client | None = None

    def guard(self, tool: str) -> None:
        if not self.tools.is_read(tool):
            kind = "write" if tool in self.tools.write else "unknown"
            raise ToolNotAllowed(f"refused {kind} tool {tool!r}: v0.1 is observe-only (see tool_policy.yaml)")

    async def __aenter__(self) -> Self:
        http = create_mcp_http_client(auth=self._auth, timeout=httpx.Timeout(self._timeout, read=self._timeout))
        self._client = Client(_HttpTransport(self.url, http), read_timeout_seconds=self._timeout)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client is not None:
            await self._client.__aexit__(*exc)
            self._client = None

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call a read tool and return its JSON (or text) result, redacted. Raises on tool errors."""
        self.guard(tool)
        if self._client is None:
            raise RuntimeError("use `async with EpiphanClient(...)`")
        result = await self._client.call_tool(tool, arguments or {})
        text = "".join(getattr(c, "text", "") for c in result.content or ())
        if result.is_error:
            raise RuntimeError(f"{tool}: {redact(text)[:500]}")
        if result.structured_content is not None:
            return redact(result.structured_content)
        try:
            return redact(json.loads(text))
        except ValueError:
            return redact(text)


class _Bearer(httpx.Auth):
    def __init__(self, token: str):
        self._token = token

    def auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request
