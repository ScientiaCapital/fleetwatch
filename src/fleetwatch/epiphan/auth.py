"""Sign in to Epiphan Edge once; keep the token in the store `token_store.py` picks (Keychain, systemd-creds, file).

The MCP SDK does the OAuth dance (discovery, PKCE, refresh). We supply where to keep the token, how to show
the sign-in link, how to catch the redirect, and one nudge: Epiphan's server admits anonymous sessions and
reports "401" inside the tool result rather than as an HTTP 401, so the SDK's flow never starts on its own.
`LoginAuth` hands the SDK a synthetic 401 for the first request when no token is stored; after that the SDK
runs the real flow, and every later run uses the saved token and the SDK's normal refresh.

Works headless (Raspberry Pi over SSH): the link is printed, and the redirect can be pasted back.
"""

import asyncio
import logging
import os
import sys
import threading
import webbrowser
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Literal
from urllib.parse import parse_qs, urlparse

import httpx2
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata, OAuthMetadata, ProtectedResourceMetadata
from pydantic import ValidationError

from fleetwatch.epiphan.token_store import FileTokenStorage, RefreshLock, TokenStore
from fleetwatch.redact import MASK, redact

__all__ = [
    "FileTokenStorage",
    "LoginAuth",
    "Revocation",
    "TokenStore",
    "make_provider",
    "parse_callback",
    "revoke_tokens",
]

LOGIN_TIMEOUT_S = 300
CALLBACK_PATH = "/callback"
# What the user sees when the server redirects back with an error. Fixed text: the redirect's own
# error_description is unvalidated (anyone can send the browser here), so it is never shown.
SIGN_IN_REFUSED = "Epiphan Edge did not complete the sign-in. Run  fleetwatch login  again."


def _first(params: dict[str, list[str]], key: str) -> str | None:
    return (params.get(key) or [None])[0]


def _query(url_or_query: str) -> dict[str, list[str]]:
    text = url_or_query.strip()
    return parse_qs(urlparse(text).query if "?" in text else text.lstrip("?"))


SIGN_IN_EXPIRED = "Sign-in expired: run fleetwatch login"

log = logging.getLogger(__name__)


def parse_callback(url_or_query: str) -> AuthorizationCodeResult | None:
    """The redirect URL (or just its query string) → code, state and the RFC 9207 `iss`, or None without both a
    code and a state. The SDK checks the state and the issuer."""
    params = _query(url_or_query)
    code, state = _first(params, "code"), _first(params, "state")
    if not code or not state:
        return None
    return AuthorizationCodeResult(code=code, state=state, iss=_first(params, "iss"))


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
            def _reply(self, status: int, body: str) -> None:
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                # Only /callback counts. A favicon request or a stray tab must not end the wait.
                if urlparse(self.path).path != CALLBACK_PATH:
                    self._reply(404, "Not found")
                    return
                params = _query(self.path)
                got = parse_callback(self.path)
                if got:
                    collector.result = got
                    self._reply(200, "Signed in to Epiphan Edge. You can close this tab and go back to the terminal.")
                elif _first(params, "error") and _first(params, "state"):
                    collector.error = SIGN_IN_REFUSED
                    self._reply(200, SIGN_IN_REFUSED)
                else:  # neither code+state nor error+state: not a real redirect, keep waiting
                    self._reply(400, "This isn't a sign-in redirect. Fleetwatch is still waiting.")
                    return
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
                print("That doesn't look like the redirect URL (it should contain code=... and state=...). Try again:")
        except (OSError, ValueError):
            return

    async def wait(self) -> AuthorizationCodeResult:
        threading.Thread(target=self._serve, daemon=True).start()
        if self.prompt:
            print(
                "After you sign in, the browser lands on a 127.0.0.1 page. If that page can't load (for example\n"
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


class _Provider(OAuthClientProvider):
    """The SDK provider, plus what it forgets across a restart, kept in the token store (`token_store.py`):

    - when the stored token expires. The SDK works it out from `expires_in` only when a token arrives, so a token
      loaded from storage looks valid forever and is never refreshed early.
    - the OAuth endpoints discovered at sign-in. The SDK discovers them only after an HTTP 401, which Epiphan never
      sends, so after a restart a refresh would go to a guessed `<origin>/token`.

    It also notices when Epiphan refuses the refresh token (HTTP 400 or 401, typically `invalid_grant`). That
    can't fix itself, so the store is marked dead and `fleetwatch run` stops instead of restarting forever.

    And it refreshes one process at a time. `run` and a cron `digest` can wake with the same expired token, and
    Epiphan rotates the refresh token, so the second to send it would get invalid_grant (and could then save a
    stale token over the first one's). Before a refresh it takes the store's lock and re-reads the store: if the
    other process got there first, its token is used and no refresh goes out."""

    dead = False  # Epiphan refused the refresh token; only `fleetwatch login` helps
    _refresh_lock: RefreshLock | None = None  # held from just before a refresh until its response is saved

    async def _auth_flow(self, request: httpx2.Request) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        """The SDK's flow, with the cross-process lock and the re-read in front of a refresh."""
        if not self._initialized:
            await self._initialize()
        if not self.context.is_token_valid() and self.context.can_refresh_token():
            lock = _ask(self.context.storage, "refresh_lock")
            if lock is not None:
                await lock.acquire()
                self._refresh_lock = lock
            await self._adopt_newer_token()
        flow = super()._auth_flow(request)
        try:
            req = await flow.__anext__()
            while True:
                req = await flow.asend((yield req))
        except StopAsyncIteration:
            return
        finally:
            self._unlock()  # a flow closed mid-refresh (network error) must not keep the other process waiting
            await flow.aclose()

    async def _adopt_newer_token(self) -> None:
        """Another process may have refreshed while we waited for the lock: its token is in the store, ours is
        spent. Take it; if its access token is still good the SDK then skips the refresh altogether."""
        storage = self.context.storage
        fresh = await storage.get_tokens()
        mine = self.context.current_tokens
        if fresh is None or mine is None or fresh.refresh_token == mine.refresh_token:
            return
        self.context.current_tokens = fresh
        when = _ask(storage, "expires_at")
        self.context.token_expiry_time = when.timestamp() if when is not None else None
        self.dead = bool(_ask(storage, "is_dead"))
        log.info("Another Fleetwatch process refreshed the Epiphan token first; using it")

    def _unlock(self) -> None:
        lock, self._refresh_lock = self._refresh_lock, None
        if lock is not None:
            lock.release()

    async def _initialize(self) -> None:
        await super()._initialize()
        storage = self.context.storage
        when = _ask(storage, "expires_at")
        if self.context.current_tokens is not None and when is not None:
            self.context.token_expiry_time = when.timestamp()
        self.dead = self.dead or bool(_ask(storage, "is_dead"))
        if self.context.oauth_metadata is None:
            self._restore_endpoints(_ask(storage, "oauth_metadata"))
        if self.context.oauth_metadata is None and self.context.can_refresh_token():
            log.warning(
                "No saved Epiphan sign-in endpoints, so a token refresh will guess %s/token. "
                "Run fleetwatch login once to save them.",
                self.context.get_authorization_base_url(self.context.server_url),
            )

    def _restore_endpoints(self, saved: dict | None) -> None:
        if not saved:
            return
        try:
            if saved.get("metadata"):
                self.context.oauth_metadata = OAuthMetadata.model_validate(saved["metadata"])
            if saved.get("protected_resource"):
                prm = ProtectedResourceMetadata.model_validate(saved["protected_resource"])
                self.context.protected_resource_metadata = prm
            self.context.auth_server_url = saved.get("auth_server_url") or self.context.auth_server_url
        except ValidationError:
            log.warning("The saved Epiphan sign-in endpoints don't parse; ignoring them")

    def _save_endpoints(self) -> None:
        """Endpoints only: issuer, token endpoint, resource. Never a token."""
        save = getattr(self.context.storage, "set_oauth_metadata", None)
        if self.context.oauth_metadata is None or not callable(save):
            return
        prm = self.context.protected_resource_metadata
        save(
            {
                "metadata": self.context.oauth_metadata.model_dump(mode="json", exclude_none=True),
                "auth_server_url": self.context.auth_server_url,
                "protected_resource": prm.model_dump(mode="json", exclude_none=True) if prm else None,
            }
        )

    async def _handle_token_response(self, response: httpx2.Response) -> None:
        await super()._handle_token_response(response)
        self._save_endpoints()

    async def _handle_refresh_response(self, response: httpx2.Response) -> bool:
        try:
            ok = await super()._handle_refresh_response(response)
        finally:
            self._unlock()  # the new token is saved (or the refresh failed): the lock covers no more than that
        if ok:
            self.dead = False
            self._save_endpoints()
        elif response.status_code in (400, 401):
            self.dead = True
            mark = getattr(self.context.storage, "mark_dead", None)
            if callable(mark):
                mark()
            log.error("Epiphan refused the refresh token (HTTP %s). %s", response.status_code, SIGN_IN_EXPIRED)
        return ok

    async def mark_expired(self) -> bool:
        """Make the next request refresh first, through the SDK's own refresh path. False if it can't refresh."""
        if not self._initialized:  # never sent a request yet, or a failed refresh dropped the tokens: reload
            await self._initialize()
        if self.dead or not self.context.can_refresh_token():
            return False
        self.context.token_expiry_time = 1.0  # in the past; 0 or None would mean "never expires" to the SDK
        return True


def _ask(storage: object, method: str):
    """Call an optional store method; stores written for the SDK alone don't have them."""
    fn = getattr(storage, method, None)
    return fn() if callable(fn) else None


def make_provider(server_url: str, storage: TokenStore, port: int, interactive: bool) -> OAuthClientProvider:
    # RFC 8252 §7.3: the loopback IP literal, not "localhost", which a resolver could point elsewhere. The callback
    # server binds the same 127.0.0.1.
    redirect = f"http://127.0.0.1:{port}{CALLBACK_PATH}"
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

    return _Provider(
        server_url=server_url,
        client_metadata=metadata,
        storage=storage,
        redirect_handler=show_link,
        callback_handler=wait_for_code,
    )


REVOKE_TIMEOUT_S = 5.0
REASON_MAX = 120  # characters of Epiphan's error text kept in the logout message


@dataclass(frozen=True)
class Revocation:
    """What `revoke_tokens` managed. `reason` is short, plain and already redacted: safe to print."""

    outcome: Literal["revoked", "no_endpoint", "failed", "no_tokens"]
    reason: str | None = None


def _safe(text: str, secrets: tuple[str, ...]) -> str:
    """Error text fit to print: our own token values masked even when nothing names them, then `redact()`."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, MASK)
    text = redact(" ".join(text.split()))
    return text if len(text) <= REASON_MAX else text[: REASON_MAX - 3] + "..."


def _https(url: str) -> bool:
    """Tokens only ever go over TLS. No loopback exception: nothing real needs one, and tests mock the transport."""
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.hostname)


def _metadata_url(issuer: str) -> str:
    """RFC 8414 section 3.1: the well-known part goes between the host and the issuer's path."""
    parsed = urlparse(issuer)
    return f"https://{parsed.netloc}/.well-known/oauth-authorization-server{parsed.path.rstrip('/')}"


def _why(response: httpx2.Response) -> str:
    """HTTP status plus the OAuth error fields (RFC 6749 section 5.2) or the start of the body. Not yet redacted."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and body.get("error"):
        detail = " ".join(str(body[k]) for k in ("error", "error_description") if body.get(k))
    else:
        detail = response.text
    return f"HTTP {response.status_code}{': ' + detail if detail.strip() else ''}"


def _error_text(e: Exception) -> str:
    """`ValueError` carries our own words; a network error says what kind it was. Not yet redacted."""
    if isinstance(e, ValueError):
        return str(e)
    return f"{type(e).__name__}: {e}" if str(e) else type(e).__name__


async def _discover(client: httpx2.AsyncClient, issuer: str) -> str | None:
    """The revocation endpoint from a fresh RFC 8414 read, or None if Epiphan publishes none. Raises ValueError when
    the answer can't be trusted (another issuer), httpx2.HTTPError when it can't be read."""
    response = await client.get(_metadata_url(issuer))
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise ValueError(_why(response))
    try:
        found = OAuthMetadata.model_validate(response.json())
    except (ValueError, ValidationError) as e:
        raise ValueError("its sign-in settings don't parse") from e
    if str(found.issuer).rstrip("/") != issuer.rstrip("/"):  # RFC 8414 section 3.3
        raise ValueError("its sign-in settings name a different issuer")
    return str(found.revocation_endpoint) if found.revocation_endpoint else None


async def revoke_tokens(
    storage: TokenStore, timeout: float = REVOKE_TIMEOUT_S, transport: httpx2.AsyncBaseTransport | None = None
) -> Revocation:
    """Ask Epiphan to revoke the stored tokens (RFC 7009): the refresh token first, then the access token.

    Uses the revocation endpoint saved at sign-in, or one from a fresh RFC 8414 read of the saved issuer. Only reads
    the store; clearing it is the caller's job, and the caller does it whatever this returns. Never raises for
    an HTTP or network problem: that comes back as "failed" with a short, redacted reason."""
    tokens = await storage.get_tokens()
    if tokens is None:
        return Revocation("no_tokens")
    client_info = await storage.get_client_info()
    secrets = (tokens.access_token, tokens.refresh_token or "", (client_info and client_info.client_secret) or "")
    saved = (_ask(storage, "oauth_metadata") or {}).get("metadata") or {}
    issuer, endpoint = saved.get("issuer"), saved.get("revocation_endpoint")
    if not issuer and not endpoint:
        return Revocation("failed", "this sign-in has no saved Epiphan addresses")

    async with httpx2.AsyncClient(transport=transport, timeout=timeout, follow_redirects=False) as client:
        try:
            if not endpoint:
                if not _https(issuer):
                    return Revocation("failed", "Epiphan's sign-in address isn't https")
                endpoint = await _discover(client, issuer)
                if endpoint is None:
                    return Revocation("no_endpoint")
        except (httpx2.HTTPError, ValueError) as e:
            return Revocation("failed", _safe(f"couldn't read Epiphan's sign-in settings: {_error_text(e)}", secrets))
        if not _https(endpoint):
            return Revocation("failed", "Epiphan's revocation address isn't https")

        problem = None
        for token, hint in ((tokens.refresh_token, "refresh_token"), (tokens.access_token, "access_token")):
            if not token:
                continue
            form = {"token": token, "token_type_hint": hint}
            if client_info is not None and client_info.client_id:
                form["client_id"] = client_info.client_id
                if client_info.client_secret:
                    form["client_secret"] = client_info.client_secret
            try:
                response = await client.post(endpoint, data=form)
            except httpx2.HTTPError as e:
                problem = problem or _error_text(e)
                continue
            if response.status_code != 200:  # RFC 7009 section 2.2: 200 means done, even for an unknown token
                problem = problem or _why(response)
    if problem:
        return Revocation("failed", _safe(problem, secrets))
    return Revocation("revoked")


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
