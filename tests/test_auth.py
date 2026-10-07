import json
from pathlib import Path

import httpx2
import pytest

from proav_agent.epiphan.auth import FileTokenStorage, LoginAuth, parse_callback


def test_parse_callback_shapes():
    assert parse_callback("http://localhost:8765/callback?code=abc&state=xyz").code == "abc"
    assert parse_callback("  ?code=abc&state=xyz\n").state == "xyz"
    assert parse_callback("code=only") is not None
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
    from proav_agent.epiphan import auth

    for k in ("DISPLAY", "WAYLAND_DISPLAY", "SSH_CONNECTION"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert isinstance(auth._headless(), bool)
