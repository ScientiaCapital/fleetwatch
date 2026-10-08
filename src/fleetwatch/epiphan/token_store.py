"""Where the Epiphan OAuth token lives: a mode-600 file, the macOS Keychain, or a systemd-creds encrypted file.

Every store keeps the same JSON (the token and the registered client) and offers what the MCP SDK's
`TokenStorage` needs plus `has_tokens()` and `clear()`. The token refreshes itself, so a store must be writable:
that's why Linux uses `systemd-creds encrypt` rather than a read-only `LoadCredential`.

The secret never goes in a process's argv, where other users could read it with `ps`. Keychain writes go to
`security -i` on stdin, hex-encoded; reads come back on stdout. `security -i` reads at most 4095 characters a
line, so the JSON is split across a few items (`part-0`, `part-1`, ...). systemd-creds reads and writes on
stdin/stdout too.

`FLEETWATCH_TOKEN_STORE=auto` picks the Keychain on macOS, systemd-creds on Linux with systemd 256 or later
(the first with `--user`), and the file everywhere else, Docker included. A token already in the file moves into
the new store on first use, and the file is deleted.
"""

import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from mcp.client.auth import TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

STORES = ("auto", "file", "keychain", "systemd-creds")
SERVICE = "fleetwatch-epiphan"  # Keychain service name and systemd-creds credential name
CHUNK = 1500  # bytes of JSON per Keychain item: 3000 hex chars, well inside a 4095-char `security -i` line
NOT_FOUND = 44  # `security` exit code for "item not found"
TIMEOUT_S = 30


class TokenStoreError(RuntimeError):
    pass


def _private_dir(folder: Path) -> None:
    """Create the token folder 0700. A folder that already exists is left as it is: we tighten only what we made."""
    if folder.exists():
        return
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(folder, 0o700)  # mkdir's mode is masked by the umask; this only ever removes bits from 0700


def _create_private(path: Path) -> int:
    """A fresh 0600 file for writing, never through a symlink. A stale file (or a link planted) there goes first, so
    O_CREAT really creates and the mode applies."""
    path.unlink(missing_ok=True)
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)


@contextmanager
def _umask(mask: int) -> Iterator[None]:
    old = os.umask(mask)
    try:
        yield
    finally:
        os.umask(old)


@runtime_checkable
class TokenStore(TokenStorage, Protocol):
    where: str  # for people: a path, or which keychain

    def has_tokens(self) -> bool: ...

    def expires_at(self) -> datetime | None: ...

    def clear(self) -> None: ...


class _JsonStore(TokenStorage):
    """The SDK methods over one JSON dict; subclasses supply `_load`, `_save` and `_drop`."""

    legacy: Path | None = None  # the old token file, moved in on first use

    def _load(self) -> dict:
        raise NotImplementedError

    def _save(self, data: dict) -> None:
        raise NotImplementedError

    def _drop(self) -> None:
        raise NotImplementedError

    def _read(self, migrate: bool = True) -> dict:
        data = self._load()
        if not data and self.legacy is not None:
            data = FileTokenStorage(self.legacy)._load()
            if data and migrate:
                self._save(data)
                self.legacy.unlink(missing_ok=True)
        return data

    async def get_tokens(self) -> OAuthToken | None:
        raw = self._read().get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        # `expires_in` is relative to when the token was issued, so it means nothing after a restart. Keep the
        # absolute time too: the provider (auth.py) restores it, and the SDK then refreshes before it runs out.
        data.pop("expires_at", None)
        if tokens.expires_in is not None:
            at = datetime.fromtimestamp(time.time() + int(tokens.expires_in), UTC)
            data["expires_at"] = at.strftime("%Y-%m-%dT%H:%M:%SZ")
        self._save(data)

    def expires_at(self) -> datetime | None:
        """When the stored access token runs out (UTC), or None if unknown. Only looks; never migrates."""
        raw = self._read(migrate=False).get("expires_at")
        try:
            return datetime.fromisoformat(raw).astimezone(UTC) if isinstance(raw, str) else None
        except ValueError:
            return None

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = self._read().get("client")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._read()
        data["client"] = client_info.model_dump(mode="json", exclude_none=True)
        self._save(data)

    def has_tokens(self) -> bool:
        return bool(self._read(migrate=False).get("tokens"))  # only looks: `doctor` and `status` change nothing

    def clear(self) -> None:
        self._drop()
        if self.legacy is not None:
            self.legacy.unlink(missing_ok=True)


class FileTokenStorage(_JsonStore):
    """One JSON file, mode 0600, holding the token and the registered client."""

    def __init__(self, path: Path):
        self.path = path
        self.where = str(path)

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        _private_dir(self.path.parent)
        tmp = self.path.with_suffix(".tmp")
        with os.fdopen(_create_private(tmp), "w") as f:
            f.write(json.dumps(data))
        tmp.replace(self.path)

    def _drop(self) -> None:
        self.path.unlink(missing_ok=True)


def _run(argv: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        raise TokenStoreError(f"{argv[0]}: {e}") from e


class KeychainTokenStorage(_JsonStore):
    """Generic-password items in the login keychain, written by /usr/bin/security (which the items then trust)."""

    def __init__(self, service: str = SERVICE, legacy: Path | None = None, keychain: str | None = None):
        self.service, self.legacy = service, legacy
        self._keychain = [keychain] if keychain else []  # a test keychain file; normally the default one
        self.where = f"macOS Keychain (service {service})"

    def _load(self) -> dict:
        parts = []
        for i in itertools.count():
            r = _run(
                ["security", "find-generic-password", "-s", self.service, "-a", f"part-{i}", "-w", *self._keychain]
            )
            if r.returncode == NOT_FOUND:
                break
            if r.returncode:
                raise TokenStoreError(f"Keychain: {r.stderr.strip() or f'security exited {r.returncode}'}")
            parts.append(r.stdout.rstrip("\n"))
        try:
            return json.loads("".join(parts)) if parts else {}
        except ValueError:  # half-written: treat as signed out
            return {}

    def _save(self, data: dict) -> None:
        text = json.dumps(data)  # ASCII only, so a chunk boundary never splits a character
        chunks = [text[i : i + CHUNK] for i in range(0, len(text), CHUNK)]
        kc = " ".join(self._keychain)
        script = "".join(
            f"add-generic-password -U -s {self.service} -a part-{i} -X {c.encode().hex()} {kc}\n"
            for i, c in enumerate(chunks)
        )
        r = _run(["security", "-i"], stdin=script)
        if r.returncode:
            raise TokenStoreError(f"Keychain: couldn't save the token (security exited {r.returncode})")
        self._drop(start=len(chunks))

    def _drop(self, start: int = 0) -> None:
        for i in itertools.count(start):
            r = _run(["security", "delete-generic-password", "-s", self.service, "-a", f"part-{i}", *self._keychain])
            if r.returncode == NOT_FOUND:
                return
            if r.returncode:
                raise TokenStoreError(f"Keychain: {r.stderr.strip() or f'security exited {r.returncode}'}")


class SystemdCredsTokenStorage(_JsonStore):
    """A file encrypted with `systemd-creds --user`: only this user on this machine can decrypt it."""

    def __init__(self, path: Path, legacy: Path | None = None):
        self.path, self.legacy = path, legacy
        self.where = f"{path} (systemd-creds, encrypted)"

    def _creds(self, verb: str, src: str, dst: str, stdin: str | None = None) -> str:
        r = _run(["systemd-creds", verb, "--user", f"--name={SERVICE}", src, dst], stdin=stdin)
        if r.returncode:
            raise TokenStoreError(f"systemd-creds {verb}: {r.stderr.strip() or f'exited {r.returncode}'}")
        return r.stdout

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self._creds("decrypt", str(self.path), "-"))
        except ValueError:
            return {}

    def _save(self, data: dict) -> None:
        _private_dir(self.path.parent)
        tmp = self.path.with_suffix(".tmp")
        os.close(_create_private(tmp))  # exists at 0600 before systemd-creds writes into it
        with _umask(0o077):  # and if it replaces the file instead, what it creates is 0600 too
            self._creds("encrypt", "-", str(tmp), stdin=json.dumps(data))
        os.chmod(tmp, 0o600)  # never wider than before; only ever tightens
        tmp.replace(self.path)

    def _drop(self) -> None:
        self.path.unlink(missing_ok=True)


def systemd_creds_version() -> int | None:
    """The systemd version behind `systemd-creds`, or None when it isn't installed."""
    if not shutil.which("systemd-creds"):
        return None
    try:
        r = subprocess.run(["systemd-creds", "--version"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"systemd (\d+)", r.stdout)
    return int(m.group(1)) if m else None


def make_token_store(kind: str, token_file: Path) -> TokenStore:
    """Build the store `FLEETWATCH_TOKEN_STORE` names. `token_file` is the file store, and the file others move from."""
    if kind not in STORES:
        raise ValueError(f"FLEETWATCH_TOKEN_STORE={kind!r}: use one of {', '.join(STORES)}")
    has_creds = sys.platform == "linux" and (systemd_creds_version() or 0) >= 256
    if kind == "auto":
        if sys.platform == "darwin" and shutil.which("security"):
            kind = "keychain"
        else:
            kind = "systemd-creds" if has_creds else "file"
    if kind == "keychain":
        if sys.platform != "darwin":
            raise ValueError("FLEETWATCH_TOKEN_STORE=keychain works on macOS only")
        return KeychainTokenStorage(legacy=token_file)
    if kind == "systemd-creds":
        if not has_creds:
            raise ValueError("FLEETWATCH_TOKEN_STORE=systemd-creds needs Linux with systemd 256 or later")
        return SystemdCredsTokenStorage(token_file.with_suffix(".cred"), legacy=token_file)
    return FileTokenStorage(token_file)
