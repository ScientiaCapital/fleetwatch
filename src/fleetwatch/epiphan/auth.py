"""Sign in to Epiphan Edge once; keep the token in the store `token_store.py` picks (Keychain, systemd-creds, file).

The MCP SDK does the OAuth dance (discovery, PKCE, refresh). We supply where to keep the token, how to show
the sign-in link, how to catch the redirect, and one nudge: Epiphan's server admits anonymous sessions and
reports "401" inside the tool result rather than as an HTTP 401, so the SDK's flow never starts on its own.
`LoginAuth` hands the SDK a synthetic 401 for the first request when no token is stored; after that the SDK
runs the real flow, and every later run uses the saved token and the SDK's normal refresh.

Works headless (Raspberry Pi over SSH): the link is printed, and the redirect can be pasted back.
"""

import asyncio
import os
import sys
import threading
import webbrowser
from collections.abc import AsyncGenerator
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import httpx2
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata

from fleetwatch.epiphan.token_store import FileTokenStorage, TokenStore

__all__ = ["FileTokenStorage", "LoginAuth", "TokenStore", "make_provider", "parse_callback"]

LOGIN_TIMEOUT_S = 300


def parse_callback(url_or_query: str) -> AuthorizationCodeResult | None:
    """The redirect URL (or just its query string) → code and state, or None if it has no code."""
    text = url_or_query.strip()
    query = urlparse(text).query if "?" in text else text.lstrip("?")
    params = parse_qs(query)
    if not params.get("code"):
        return None
    return AuthorizationCodeResult(code=params["code"][0], state=(params.get("state") or [None])[0])


class _Redirect:
    """Collects the authorization code from whichever arrives first: the browser hitting our local
    callback, or the user pasting the redirect URL at the prompt."""

    def __init__(self, port: int, prompt: bool):
        self.port, self.prompt = port, prompt
        self.result: AuthorizationCodeResult | None = None
        self.error: str | None = None
        self._done = threading.Event()

    def _serve(self) -> None:
        collector = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                got = parse_callback(self.path)
                if got:
                    collector.result = got
                    body = "Signed in to Epiphan Edge. You can close this tab and go back to the terminal."
                else:
                    q = parse_qs(urlparse(self.path).query)
                    collector.error = (q.get("error_description") or q.get("error") or ["no code in the redirect"])[0]
                    body = f"Sign-in did not finish: {collector.error}"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode())
                collector._done.set()

            def log_message(self, *_):  # keep the terminal quiet
                pass

        try:
            with HTTPServer(("127.0.0.1", self.port), Handler) as server:
                server.timeout = 1
                while not self._done.is_set():
                    server.handle_request()
        except OSError as e:  # port busy: the paste prompt still works
            if self.prompt:
                print(f"(couldn't listen on port {self.port}: {e}; paste the redirect URL instead)")

    def _read_paste(self) -> None:
        try:
            while not self._done.is_set():
                line = sys.stdin.readline()
                if not line:
                    return
                got = parse_callback(line)
                if got:
                    self.result = got
                    self._done.set()
                    return
                print("That doesn't look like the redirect URL (it should contain code=...). Try again:")
        except (OSError, ValueError):
            return

    async def wait(self) -> AuthorizationCodeResult:
        threading.Thread(target=self._serve, daemon=True).start()
        if self.prompt:
            print(
                "After you sign in, the browser lands on a localhost page. If that page can't load (for example\n"
                "when the agent runs on a Raspberry Pi), paste the full URL from the address bar here and press Enter."
            )
            threading.Thread(target=self._read_paste, daemon=True).start()
        for _ in range(LOGIN_TIMEOUT_S * 2):
            if self._done.is_set():
                break
            await asyncio.sleep(0.5)
        self._done.set()
        if self.result is None:
            raise RuntimeError(f"Sign-in did not finish: {self.error or 'no reply within 5 minutes'}")
        return self.result


def _headless() -> bool:
    if sys.platform == "darwin":
        return bool(os.environ.get("SSH_CONNECTION")) and not os.environ.get("DISPLAY")
    return not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY")


def make_provider(server_url: str, storage: TokenStore, port: int, interactive: bool) -> OAuthClientProvider:
    redirect = f"http://localhost:{port}/callback"
    metadata = OAuthClientMetadata(
        client_name="fleetwatch",
        redirect_uris=[redirect],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )

    async def show_link(url: str) -> None:
        if not interactive:
            raise RuntimeError("Epiphan sign-in has expired. Run `fleetwatch login` once, then start the agent again.")
        print("\nSign in to Epiphan Edge and pick the team to watch:\n\n  " + url + "\n")
        if not _headless():
            webbrowser.open(url)

    async def wait_for_code() -> AuthorizationCodeResult:
        return await _Redirect(port, prompt=interactive and sys.stdin.isatty()).wait()

    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=metadata,
        storage=storage,
        redirect_handler=show_link,
        callback_handler=wait_for_code,
    )


class LoginAuth(httpx2.Auth):
    """Wraps the SDK provider for `fleetwatch login`. With no stored token, the first request is answered
    with a synthetic 401 so the SDK starts its flow; the discovery, authorize and token requests it then
    yields go to the network as usual. With a token stored, it's a plain pass-through."""

    requires_response_body = True

    def __init__(self, provider: OAuthClientProvider, storage: TokenStore):
        self.provider, self.storage = provider, storage

    async def async_auth_flow(self, request: httpx2.Request) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        flow = self.provider._auth_flow(request)  # the SDK's generator; public wrapper only adds redirect handling
        force = not self.storage.has_tokens()
        try:
            req = await flow.__anext__()
            while True:
                if force and req is request:
                    force = False
                    response = httpx2.Response(401, request=req, headers={"WWW-Authenticate": "Bearer"})
                else:
                    response = yield req
                    await response.aread()
                req = await flow.asend(response)
        except StopAsyncIteration:
            return
