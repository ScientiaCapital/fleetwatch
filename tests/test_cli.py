"""CLI surface that doesn't need Epiphan."""

import json
import sys

import httpx2
import pytest

from fleetwatch import cli
from fleetwatch.epiphan import auth

ISSUER = "https://auth.example.invalid"
NO_ENDPOINT = (
    "Signed out on this machine. Epiphan doesn't offer token revocation, so a copied token works until it expires."
)
NOT_CONFIRMED = "Signed out on this machine. Epiphan didn't confirm the revocation ("


def _stored(revocation_endpoint: str | None = f"{ISSUER}/revoke") -> dict:
    meta = {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "response_types_supported": ["code"],
    }
    if revocation_endpoint:
        meta["revocation_endpoint"] = revocation_endpoint
    return {
        "tokens": {"access_token": "FAKEACCESS", "refresh_token": "FAKEREFRESH", "token_type": "Bearer"},
        "client": {"client_id": "c1", "redirect_uris": ["http://127.0.0.1:8765/callback"]},
        "oauth": {"metadata": meta, "auth_server_url": ISSUER},
    }


@pytest.fixture
def logout(monkeypatch, capsys, tmp_path):
    """Run `fleetwatch logout` on a file store in tmp_path, with Epiphan answered by `answer` (never the network).
    Returns what it printed and the requests it made, and checks the store is gone afterwards."""

    def run(data: dict | None, answer=None) -> tuple[str, list[httpx2.Request]]:
        path = tmp_path / "epiphan-oauth.json"
        if data is not None:
            path.write_text(json.dumps(data))
        monkeypatch.setenv("FLEETWATCH_TOKEN_STORE", "file")
        monkeypatch.setenv("FLEETWATCH_TOKEN_FILE", str(path))
        seen: list[httpx2.Request] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(request)
            if answer is None:
                raise AssertionError("logout made a request it shouldn't have")
            return answer(request)

        real = auth.revoke_tokens

        async def mocked(storage, **_):
            return await real(storage, transport=httpx2.MockTransport(handler))

        monkeypatch.setattr(auth, "revoke_tokens", mocked)
        monkeypatch.setattr(sys, "argv", ["fleetwatch", "logout"])
        cli.main()
        assert not path.exists(), "logout always clears the store on this machine"
        return capsys.readouterr().out.strip(), seen

    return run


def test_logout_revokes_then_clears(logout):
    out, seen = logout(_stored(), lambda r: httpx2.Response(200))
    assert out == "Signed out. Epiphan revoked the token."
    assert len(seen) == 2


def test_logout_without_a_revocation_endpoint(logout):
    meta = _stored(None)["oauth"]["metadata"]
    out, _ = logout(_stored(None), lambda r: httpx2.Response(200, json=meta))
    assert out == NO_ENDPOINT


@pytest.mark.parametrize("status", [400, 503])
def test_logout_when_revoke_is_refused(logout, status):
    out, _ = logout(_stored(), lambda r: httpx2.Response(status))
    assert out.startswith(NOT_CONFIRMED) and f"HTTP {status}" in out
    assert out.endswith("), so a copied token may work until it expires.")


def test_logout_when_epiphan_is_unreachable(logout):
    def timeout(r):
        raise httpx2.ReadTimeout("timed out")

    out, _ = logout(_stored(), timeout)
    assert out.startswith(NOT_CONFIRMED)


def test_logout_redacts_secrets_from_the_error_body(logout):
    body = {"error": "invalid_request", "error_description": "rejected access_token=FAKELEAKED123"}
    out, _ = logout(_stored(), lambda r: httpx2.Response(400, json=body))
    assert "FAKELEAKED123" not in out and "FAKEACCESS" not in out and "FAKEREFRESH" not in out
    assert "[redacted]" in out


def test_logout_with_nothing_stored_makes_no_request(logout):
    out, seen = logout(None)
    assert out == "Signed out."
    assert seen == []


def test_logout_never_raises_from_revocation(monkeypatch, capsys, tmp_path):
    path = tmp_path / "epiphan-oauth.json"
    path.write_text(json.dumps(_stored()))
    monkeypatch.setenv("FLEETWATCH_TOKEN_STORE", "file")
    monkeypatch.setenv("FLEETWATCH_TOKEN_FILE", str(path))

    async def broken(storage, **_):
        raise RuntimeError("unexpected: token=FAKELEAKED999")

    monkeypatch.setattr(auth, "revoke_tokens", broken)
    monkeypatch.setattr(sys, "argv", ["fleetwatch", "logout"])
    cli.main()
    out = capsys.readouterr().out
    assert not path.exists()
    assert out.startswith(NOT_CONFIRMED) and "FAKELEAKED999" not in out


def test_version_flag_prints_the_package_version(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["fleetwatch", "--version"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("fleetwatch ") and out.split()[1][0].isdigit()


def test_replay_digest_ignores_quiet_hours(monkeypatch, capsys):
    # A replay is a demo against an in-memory state; it must show the full digest at any hour (CI runs at night).
    from fleetwatch.policy import Policy

    monkeypatch.setattr(Policy, "in_quiet_hours", lambda self, now: self.quiet_start is not None)
    monkeypatch.setattr(sys, "argv", ["fleetwatch", "digest", "--replay", "tests/fixtures"])
    cli.main()
    out = capsys.readouterr().out
    assert "Fleet check" in out
    assert "Fix soon" in out
