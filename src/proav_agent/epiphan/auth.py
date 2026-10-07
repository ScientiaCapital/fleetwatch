"""Sign in to Epiphan Edge once; keep the token in ~/.proav-agent, readable only by this user.

The MCP SDK does the OAuth dance (discovery, PKCE, refresh). We only supply where to keep the token and
how to open the browser and catch the redirect.
"""

import asyncio
import json
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlparse

from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken


class FileTokenStorage(TokenStorage):
    """One JSON file, mode 0600, holding the token and the registered client."""

    def __init__(self, path: Path):
        self.path = path

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    async def get_tokens(self) -> OAuthToken | None:
        raw = self._read().get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        self._write(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = self._read().get("client")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._read()
        data["client"] = client_info.model_dump(mode="json", exclude_none=True)
        self._write(data)

    def has_tokens(self) -> bool:
        return bool(self._read().get("tokens"))

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class _Callback(BaseHTTPRequestHandler):
    result: AuthorizationCodeResult | None = None
    error: str | None = None

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        if "code" in query:
            _Callback.result = AuthorizationCodeResult(code=query["code"][0], state=(query.get("state") or [None])[0])
            body = "Signed in to Epiphan Edge. You can close this tab and go back to the terminal."
        else:
            _Callback.error = (query.get("error_description") or query.get("error") or ["unknown error"])[0]
            body = f"Sign-in did not finish: {_Callback.error}"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *_):  # keep the terminal quiet
        pass


def make_provider(server_url: str, storage: FileTokenStorage, port: int, interactive: bool) -> OAuthClientProvider:
    redirect = f"http://localhost:{port}/callback"
    metadata = OAuthClientMetadata(
        client_name="proav-agent",
        redirect_uris=[redirect],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )

    async def open_browser(url: str) -> None:
        if not interactive:
            raise RuntimeError("Epiphan sign-in has expired. Run `proav-agent login` once, then start the agent again.")
        print("Opening your browser to sign in to Epiphan Edge. If it doesn't open, visit:\n  " + url)
        webbrowser.open(url)

    async def wait_for_code() -> AuthorizationCodeResult:
        _Callback.result, _Callback.error = None, None
        server = HTTPServer(("localhost", port), _Callback)
        Thread(target=server.handle_request, daemon=True).start()
        for _ in range(600):  # up to 5 minutes
            if _Callback.result or _Callback.error:
                break
            await asyncio.sleep(0.5)
        server.server_close()
        if _Callback.result is None:
            raise RuntimeError(f"Sign-in did not finish: {_Callback.error or 'no reply from the browser'}")
        return _Callback.result

    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=metadata,
        storage=storage,
        redirect_handler=open_browser,
        callback_handler=wait_for_code,
    )
