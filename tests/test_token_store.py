"""Token stores: file, macOS Keychain, systemd-creds. `subprocess.run` is faked; the real keychain is never touched."""

import base64
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from fleetwatch.epiphan import token_store as ts
from fleetwatch.epiphan.token_store import (
    FileTokenStorage,
    KeychainTokenStorage,
    SystemdCredsTokenStorage,
    TokenStoreError,
    make_token_store,
)

SECRET = "FAKEACCESS-" + "x" * 5000  # bigger than one `security -i` line can hold, so it spans several items


class FakeSecurity:
    """Just enough of /usr/bin/security: generic passwords in a dict, every argv recorded."""

    def __init__(self, fail_rc: int = 0):
        self.items: dict[tuple[str, str], str] = {}
        self.argvs: list[list[str]] = []
        self.fail_rc = fail_rc

    def __call__(self, argv, input=None, **kw):
        self.argvs.append(list(argv))
        assert argv[0] == "security"
        if self.fail_rc:
            return subprocess.CompletedProcess(argv, self.fail_rc, "", "security: keychain is locked")
        if argv[1:] == ["-i"]:
            for line in input.splitlines():
                rc = self._one(shlex.split(line))
                if rc:
                    return subprocess.CompletedProcess(argv, rc, "", "failed")
            return subprocess.CompletedProcess(argv, 0, "", "")
        rc = self._one(argv[1:])
        out = self.items.get(self._key(argv[1:]), "") + "\n" if rc == 0 and "-w" in argv else ""
        return subprocess.CompletedProcess(argv, rc, out, "" if rc == 0 else "could not be found")

    @staticmethod
    def _opt(args, flag):
        return args[args.index(flag) + 1] if flag in args else None

    def _key(self, args):
        return (self._opt(args, "-s"), self._opt(args, "-a"))

    def _one(self, args) -> int:
        key = self._key(args)
        if args[0] == "add-generic-password":
            if key in self.items and "-U" not in args:
                return 45
            self.items[key] = bytes.fromhex(self._opt(args, "-X")).decode()
            return 0
        if args[0] == "find-generic-password":
            return 0 if key in self.items else 44
        if args[0] == "delete-generic-password":
            return 0 if self.items.pop(key, None) is not None else 44
        raise AssertionError(f"unexpected security command {args}")


class FakeCreds:
    """Stands in for systemd-creds: 'encrypts' with base64 so the file never holds the plain JSON."""

    def __init__(self, version: str = "systemd 256 (256.5-1)"):
        self.version = version
        self.argvs: list[list[str]] = []

    def __call__(self, argv, input=None, **kw):
        self.argvs.append(list(argv))
        assert argv[0] == "systemd-creds"
        if argv[1] == "--version":
            return subprocess.CompletedProcess(argv, 0, self.version + "\n+PAM +AUDIT\n", "")
        assert "--user" in argv and "--name=fleetwatch-epiphan" in argv
        src, dst = argv[-2], argv[-1]
        if argv[1] == "encrypt":
            assert src == "-"
            Path(dst).write_text("ENC:" + base64.b64encode(input.encode()).decode())
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1] == "decrypt":
            assert dst == "-"
            plain = base64.b64decode(Path(src).read_text().removeprefix("ENC:")).decode()
            return subprocess.CompletedProcess(argv, 0, plain, "")
        raise AssertionError(f"unexpected systemd-creds command {argv}")


def token(access: str = SECRET) -> OAuthToken:
    return OAuthToken(access_token=access, refresh_token="FAKEREFRESH")


def client() -> OAuthClientInformationFull:
    return OAuthClientInformationFull(client_id="fake-client", redirect_uris=["http://localhost:8765/callback"])


@pytest.fixture
def security(monkeypatch):
    fake = FakeSecurity()
    monkeypatch.setattr(ts.subprocess, "run", fake)
    return fake


@pytest.fixture
def creds(monkeypatch):
    fake = FakeCreds()
    monkeypatch.setattr(ts.subprocess, "run", fake)
    monkeypatch.setattr(ts.shutil, "which", lambda name: f"/usr/bin/{name}")
    return fake


# --- the factory -------------------------------------------------------------------------------------------------


def test_file_store_when_asked(tmp_path):
    s = make_token_store("file", tmp_path / "t.json")
    assert isinstance(s, FileTokenStorage) and s.where == str(tmp_path / "t.json")


def test_auto_picks_keychain_on_macos(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ts.shutil, "which", lambda name: "/usr/bin/security" if name == "security" else None)
    assert isinstance(make_token_store("auto", tmp_path / "t.json"), KeychainTokenStorage)


@pytest.mark.parametrize(
    ("version", "kind"),
    [("systemd 256 (256.5-1)", SystemdCredsTokenStorage), ("systemd 255 (255.4-1ubuntu8)", FileTokenStorage)],
)
def test_auto_on_linux_needs_systemd_256(monkeypatch, tmp_path, creds, version, kind):
    monkeypatch.setattr(sys, "platform", "linux")
    creds.version = version
    s = make_token_store("auto", tmp_path / "t.json")
    assert isinstance(s, kind)
    if kind is SystemdCredsTokenStorage:
        assert s.path == tmp_path / "t.cred"


def test_auto_without_systemd_creds_is_the_file(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ts.shutil, "which", lambda name: None)  # Docker, older distros
    assert isinstance(make_token_store("auto", tmp_path / "t.json"), FileTokenStorage)


def test_asking_for_a_store_this_machine_lacks_is_an_error(monkeypatch, tmp_path, creds):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(ValueError, match="macOS"):
        make_token_store("keychain", tmp_path / "t.json")
    creds.version = "systemd 252 (252.30)"
    with pytest.raises(ValueError, match="256"):
        make_token_store("systemd-creds", tmp_path / "t.json")
    with pytest.raises(ValueError, match="FLEETWATCH_TOKEN_STORE"):
        make_token_store("vault", tmp_path / "t.json")


def test_systemd_creds_version(creds):
    assert ts.systemd_creds_version() == 256
    creds.version = "garbage"
    assert ts.systemd_creds_version() is None


# --- Keychain ----------------------------------------------------------------------------------------------------


async def test_keychain_round_trip_keeps_the_secret_out_of_argv(security):
    s = KeychainTokenStorage()
    assert not s.has_tokens() and await s.get_tokens() is None
    await s.set_tokens(token())
    await s.set_client_info(client())
    assert s.has_tokens()
    assert (await s.get_tokens()).access_token == SECRET
    assert (await s.get_client_info()).client_id == "fake-client"
    assert len(security.items) > 1, "a large token spans several items"
    for argv in security.argvs:
        joined = " ".join(argv)
        assert "FAKEACCESS" not in joined and SECRET.encode().hex()[:64] not in joined
    assert ["security", "-i"] in security.argvs, "writes go through stdin"


async def test_keychain_drops_parts_a_shorter_token_no_longer_needs(security):
    s = KeychainTokenStorage()
    await s.set_tokens(token())
    many = len(security.items)
    await s.set_tokens(token("short"))
    assert len(security.items) < many
    assert (await s.get_tokens()).access_token == "short"


async def test_keychain_clear(security):
    s = KeychainTokenStorage()
    await s.set_tokens(token())
    s.clear()
    assert security.items == {} and not s.has_tokens()


def test_keychain_error_is_reported_not_swallowed(monkeypatch):
    monkeypatch.setattr(ts.subprocess, "run", FakeSecurity(fail_rc=51))
    with pytest.raises(TokenStoreError, match="locked"):
        KeychainTokenStorage().has_tokens()


async def test_file_token_moves_into_the_keychain_once(security, tmp_path):
    old = FileTokenStorage(tmp_path / "t.json")
    await old.set_tokens(token("FROMFILE"))
    s = KeychainTokenStorage(legacy=tmp_path / "t.json")
    assert s.has_tokens() and (tmp_path / "t.json").exists(), "has_tokens only looks; it doesn't move anything"
    assert (await s.get_tokens()).access_token == "FROMFILE"
    assert not (tmp_path / "t.json").exists(), "the plain file is gone once the token is in the keychain"
    assert (await KeychainTokenStorage().get_tokens()).access_token == "FROMFILE"


async def test_legacy_file_is_removed_once_the_keychain_holds_a_token(security, tmp_path):
    """Both hold a token (say the file came back from a backup): the keychain wins and the plain file goes."""
    legacy = tmp_path / "t.json"
    await FileTokenStorage(legacy).set_tokens(token("STALE"))
    await KeychainTokenStorage().set_tokens(token("CURRENT"))
    s = KeychainTokenStorage(legacy=legacy)
    assert s.has_tokens() and legacy.exists(), "has_tokens only looks; it doesn't remove anything"
    assert (await s.get_tokens()).access_token == "CURRENT", "the new store wins, never the file"
    assert not legacy.exists(), "the plain file is gone once the keychain is known to hold a token"


async def test_legacy_file_stays_until_the_new_store_holds_a_token(security, tmp_path):
    """The new store holds the client registration but no token yet: the file is not ours to remove."""
    legacy = tmp_path / "t.json"
    await FileTokenStorage(legacy).set_tokens(token("FROMFILE"))
    await KeychainTokenStorage().set_client_info(client())
    s = KeychainTokenStorage(legacy=legacy)
    assert await s.get_tokens() is None
    assert legacy.exists()


# --- systemd-creds -----------------------------------------------------------------------------------------------


async def test_systemd_creds_round_trip(creds, tmp_path):
    s = SystemdCredsTokenStorage(tmp_path / "t.cred")
    assert not s.has_tokens()
    await s.set_tokens(token())
    assert s.has_tokens() and (await s.get_tokens()).access_token == SECRET
    f = tmp_path / "t.cred"
    assert "FAKEACCESS" not in f.read_text(), "encrypted at rest"
    assert f.stat().st_mode & 0o777 == 0o600
    for argv in creds.argvs:
        assert "FAKEACCESS" not in " ".join(argv)
    s.clear()
    assert not f.exists() and not s.has_tokens()


async def test_file_token_moves_into_systemd_creds(creds, tmp_path):
    await FileTokenStorage(tmp_path / "t.json").set_tokens(token("FROMFILE"))
    s = SystemdCredsTokenStorage(tmp_path / "t.cred", legacy=tmp_path / "t.json")
    assert (await s.get_tokens()).access_token == "FROMFILE"
    assert not (tmp_path / "t.json").exists() and (tmp_path / "t.cred").exists()


def test_systemd_creds_error_is_reported(monkeypatch, tmp_path):
    (tmp_path / "t.cred").write_text("junk")
    monkeypatch.setattr(
        ts.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "Failed to decrypt")
    )
    with pytest.raises(TokenStoreError, match="decrypt"):
        SystemdCredsTokenStorage(tmp_path / "t.cred").has_tokens()
