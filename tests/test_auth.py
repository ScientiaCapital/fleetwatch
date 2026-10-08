import json
from pathlib import Path

import httpx2
import pytest

from fleetwatch.epiphan.auth import FileTokenStorage, LoginAuth, parse_callback


def test_parse_callback_shapes():
    assert parse_callback("http://127.0.0.1:8765/callback?code=abc&state=xyz").code == "abc"
    assert parse_callback("  ?code=abc&state=xyz\n").state == "xyz"
    assert parse_callback("code=abc&state=xyz") is not None
    assert parse_callback("code=only") is None, "no state: the SDK would refuse it, so it doesn't end the wait"
    assert parse_callback("http://localhost:8765/callback?error=denied") is None
    assert parse_callback("not a url") is None


async def test_token_file_is_private(tmp_path: Path):
    from mcp.shared.auth import OAuthToken

    s = FileTokenStorage(tmp_path / "t.json")
    assert not s.has_tokens() and await s.get_tokens() is None
    await s.set_tokens(OAuthToken(access_token="FAKEACCESS", refresh_token="FAKEREFRESH"))
    assert s.has_tokens() and (tmp_path / "t.json").stat().st_mode & 0o777 == 0o600
    assert json.loads((tmp_path / "t.json").read_text())["tokens"]["access_token"] == "FAKEACCESS"
    s.clear()
    assert not s.has_tokens()


class _FakeProvider:
    """Stands in for the SDK generator: yields the request, records the response it gets back,
    then (on a 401) yields a 'discovery' request before finishing."""

    def __init__(self):
        self.seen = []

    async def _auth_flow(self, request):
        response = yield request
        self.seen.append(response.status_code)
        if response.status_code == 401:
            r2 = yield httpx2.Request("GET", "https://example.invalid/.well-known/oauth-protected-resource")
            self.seen.append(r2.status_code)


async def _drive(auth: LoginAuth, request: httpx2.Request, network_status: int) -> list[str]:
    """Run the auth flow the way httpx would, answering every request that reaches 'the network'."""
    sent = []
    flow = auth.async_auth_flow(request)
    req = await flow.__anext__()
    try:
        while True:
            sent.append(str(req.url))
            req = await flow.asend(httpx2.Response(network_status, request=req))
    except StopAsyncIteration:
        return sent


async def test_login_forces_flow_only_when_no_token(tmp_path: Path):
    from mcp.shared.auth import OAuthToken

    storage = FileTokenStorage(tmp_path / "t.json")
    request = httpx2.Request("POST", "https://example.invalid/mcp")

    fake = _FakeProvider()
    sent = await _drive(LoginAuth(fake, storage), request, network_status=200)
    assert fake.seen == [401, 200], "first answer is the synthetic 401; discovery really went out"
    assert sent == ["https://example.invalid/.well-known/oauth-protected-resource"], (
        "the forced request never hit the network"
    )

    await storage.set_tokens(OAuthToken(access_token="x"))
    fake = _FakeProvider()
    sent = await _drive(LoginAuth(fake, storage), request, network_status=200)
    assert fake.seen == [200] and sent == ["https://example.invalid/mcp"], "with a token it's a pass-through"


@pytest.mark.parametrize("env", [{}, {"SSH_CONNECTION": "1.2.3.4"}])
def test_headless_detection_does_not_crash(monkeypatch, env):
    from fleetwatch.epiphan import auth

    for k in ("DISPLAY", "WAYLAND_DISPLAY", "SSH_CONNECTION"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert isinstance(auth._headless(), bool)


# --- Revoke on logout (RFC 7009) -------------------------------------------------------------------------------

ISSUER = "https://auth.example.invalid"
REVOKE_URL = f"{ISSUER}/oauth/revoke"
BASE_META = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/authorize",
    "token_endpoint": f"{ISSUER}/token",
    "response_types_supported": ["code"],
}


def _with_revoke(**extra) -> dict:
    return {**BASE_META, "revocation_endpoint": REVOKE_URL, **extra}


def _signed_in(tmp_path: Path, metadata: dict | None = None, client: dict | None = None, oauth: bool = True) -> Path:
    """A file store as `fleetwatch login` leaves it: token, registered client, and the endpoints it discovered."""
    data = {
        "tokens": {"access_token": "FAKEACCESS", "refresh_token": "FAKEREFRESH", "token_type": "Bearer"},
        "client": client or {"client_id": "fleetwatch-client", "redirect_uris": ["http://127.0.0.1:8765/callback"]},
    }
    if oauth:
        data["oauth"] = {"metadata": metadata or BASE_META, "auth_server_url": ISSUER, "protected_resource": None}
    path = tmp_path / "t.json"
    path.write_text(json.dumps(data))
    return path


class _Recorder:
    """An httpx2.MockTransport handler that records each request and answers from a function."""

    def __init__(self, answer):
        self.answer, self.requests = answer, []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.answer(request)

    @property
    def forms(self) -> list[dict[str, list[str]]]:
        from urllib.parse import parse_qs

        return [parse_qs(r.content.decode()) for r in self.requests if r.method == "POST"]


async def _revoke(path: Path, answer) -> tuple[object, _Recorder]:
    from fleetwatch.epiphan.auth import revoke_tokens

    rec = _Recorder(answer)
    return await revoke_tokens(FileTokenStorage(path), transport=httpx2.MockTransport(rec)), rec


async def test_revoke_posts_refresh_then_access_token(tmp_path: Path):
    result, rec = await _revoke(_signed_in(tmp_path, _with_revoke()), lambda r: httpx2.Response(200))
    assert result.outcome == "revoked"
    assert [str(r.url) for r in rec.requests] == [REVOKE_URL, REVOKE_URL]
    assert all(r.headers["content-type"] == "application/x-www-form-urlencoded" for r in rec.requests)
    refresh, access = rec.forms
    assert refresh == {
        "token": ["FAKEREFRESH"],
        "token_type_hint": ["refresh_token"],
        "client_id": ["fleetwatch-client"],
    }
    assert access == {"token": ["FAKEACCESS"], "token_type_hint": ["access_token"], "client_id": ["fleetwatch-client"]}


async def test_revoke_sends_the_client_secret_only_when_stored(tmp_path: Path):
    client = {"client_id": "c1", "client_secret": "FAKESECRET", "redirect_uris": ["http://127.0.0.1:8765/callback"]}
    result, rec = await _revoke(_signed_in(tmp_path, _with_revoke(), client=client), lambda r: httpx2.Response(200))
    assert result.outcome == "revoked"
    assert all(f["client_secret"] == ["FAKESECRET"] for f in rec.forms)


async def test_revoke_when_epiphan_offers_no_endpoint(tmp_path: Path):
    # The saved metadata has no revocation_endpoint, and a fresh discovery read has none either.
    result, rec = await _revoke(_signed_in(tmp_path), lambda r: httpx2.Response(200, json=BASE_META))
    assert result.outcome == "no_endpoint"
    assert [(r.method, str(r.url)) for r in rec.requests] == [
        ("GET", f"{ISSUER}/.well-known/oauth-authorization-server")
    ], "one discovery read, and no token sent anywhere"


async def test_revoke_when_discovery_finds_nothing(tmp_path: Path):
    result, rec = await _revoke(_signed_in(tmp_path), lambda r: httpx2.Response(404))
    assert result.outcome == "no_endpoint"
    assert [r.method for r in rec.requests] == ["GET"]


async def test_revoke_discovers_the_endpoint_from_the_issuer(tmp_path: Path):
    def answer(r: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_with_revoke()) if r.method == "GET" else httpx2.Response(200)

    result, rec = await _revoke(_signed_in(tmp_path), answer)
    assert result.outcome == "revoked"
    assert [r.method for r in rec.requests] == ["GET", "POST", "POST"]


async def test_revoke_ignores_discovery_for_another_issuer(tmp_path: Path):
    other = _with_revoke(issuer="https://other.example.invalid", revocation_endpoint="https://other.example.invalid/r")
    result, rec = await _revoke(_signed_in(tmp_path), lambda r: httpx2.Response(200, json=other))
    assert result.outcome == "failed"
    assert [r.method for r in rec.requests] == ["GET"], "no token goes to an endpoint another issuer named"


async def test_revoke_without_saved_endpoints_says_so(tmp_path: Path):
    result, rec = await _revoke(_signed_in(tmp_path, oauth=False), lambda r: httpx2.Response(200))
    assert result.outcome == "failed" and result.reason
    assert rec.requests == []


async def test_revoke_with_no_tokens_makes_no_request(tmp_path: Path):
    result, rec = await _revoke(tmp_path / "none.json", lambda r: httpx2.Response(200))
    assert result.outcome == "no_tokens"
    assert rec.requests == []


@pytest.mark.parametrize("status", [400, 401, 500, 503])
async def test_revoke_error_status_is_a_failure(tmp_path: Path, status: int):
    answer = lambda r: httpx2.Response(status, json={"error": "invalid_client"})
    result, rec = await _revoke(_signed_in(tmp_path, _with_revoke()), answer)
    assert result.outcome == "failed"
    assert f"HTTP {status}" in result.reason and "invalid_client" in result.reason
    assert len(rec.requests) == 2, "the access token is still sent after the refresh token fails"


async def test_revoke_does_not_follow_a_redirect(tmp_path: Path):
    answer = lambda r: httpx2.Response(302, headers={"Location": "https://elsewhere.example.invalid/"})
    result, rec = await _revoke(_signed_in(tmp_path, _with_revoke()), answer)
    assert result.outcome == "failed"
    assert all(str(r.url) == REVOKE_URL for r in rec.requests)


@pytest.mark.parametrize("exc", [httpx2.ConnectTimeout("timed out"), httpx2.ConnectError("no route")])
async def test_revoke_network_error_is_a_failure(tmp_path: Path, exc: Exception):
    def answer(r: httpx2.Request) -> httpx2.Response:
        raise exc

    result, _ = await _revoke(_signed_in(tmp_path, _with_revoke()), answer)
    assert result.outcome == "failed" and result.reason


@pytest.mark.parametrize("url", ["http://auth.example.invalid/revoke", "http://127.0.0.1:9/revoke", "ftp://x/revoke"])
async def test_revoke_refuses_an_endpoint_that_is_not_https(tmp_path: Path, url: str):
    result, rec = await _revoke(
        _signed_in(tmp_path, _with_revoke(revocation_endpoint=url)), lambda r: httpx2.Response(200)
    )
    assert result.outcome == "failed" and "https" in result.reason
    assert rec.requests == [], "a token never goes over plain http"


async def test_revoke_failure_reason_is_redacted(tmp_path: Path):
    body = {"error": "invalid_request", "error_description": "bad token=FAKELEAKED123 and FAKEREFRESH echoed"}
    result, _ = await _revoke(_signed_in(tmp_path, _with_revoke()), lambda r: httpx2.Response(400, json=body))
    assert result.outcome == "failed"
    assert "FAKELEAKED123" not in result.reason and "FAKEREFRESH" not in result.reason
    assert "[redacted]" in result.reason


async def test_revoke_leaves_the_store_alone(tmp_path: Path):
    """Clearing is the CLI's job, after revoking; the helper only reads."""
    path = _signed_in(tmp_path, _with_revoke())
    await _revoke(path, lambda r: httpx2.Response(200))
    assert FileTokenStorage(path).has_tokens()
