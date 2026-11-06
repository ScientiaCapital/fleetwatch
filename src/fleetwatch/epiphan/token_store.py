"""Where the Epiphan OAuth token lives: a mode-600 file, the macOS Keychain, or a systemd-creds encrypted file.

Every store keeps the same JSON (the token and the registered client) and offers what the MCP SDK's
`TokenStorage` needs plus `has_tokens()` and `clear()`. The token refreshes itself, so a store must be writable:
that's why Linux uses `systemd-creds encrypt` rather than a read-only `LoadCredential`.

The secret never goes in a process's argv, where other users could read it with `ps`. Keychain writes go to
`security -i` on stdin, hex-encoded; reads come back on stdout. `security -i` reads at most 4095 characters a
line, so the JSON is split across a few items. Each save writes a new generation of them (`g-<id>-0`, `g-<id>-1`,
...) and then points the `index` item at it, so a reader never sees half of one save and half of another.
systemd-creds reads and writes on stdin/stdout too.

`FLEETWATCH_TOKEN_STORE=auto` picks the Keychain on macOS, systemd-creds on Linux with systemd 256 or later
(the first with `--user`), and the file everywhere else, Docker included. A token already in the file moves into
the new store on first use, and the file is deleted. A file found later, beside a store that already holds a
token, is stale and is deleted too: the store is the copy that gets refreshed.
"""

import asyncio
import fcntl
import itertools
import json
import logging
import os
import re
import secrets
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
SANDBOX_SERVICE = "fleetwatch-epiphan-sandbox"  # the v0.2 sandbox sign-in's own slot, never shared with SERVICE
CHUNK = 1500  # bytes of JSON per Keychain item: 3000 hex chars, well inside a 4095-char `security -i` line
NOT_FOUND = 44  # `security` exit code for "item not found"
INDEX = "index"  # the Keychain item naming the current generation of parts
READ_ATTEMPTS = 3  # a reader that keeps landing mid-save gives up after this many tries
TIMEOUT_S = 30
LOCK_WAIT_S = 30  # how long a refresh waits for another process's refresh; then this beat fails, not refreshes

log = logging.getLogger(__name__)


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


class RefreshBusy(RuntimeError):
    """Another process held the refresh lock too long. Refreshing anyway could spend its refresh token (#162)."""


class RefreshLock:
    """One token refresh at a time across processes (`run` plus a cron `digest` or `sweep`): an flock on an empty
    0600 file beside the token. Epiphan rotates the refresh token, so the second process to send the same one
    gets invalid_grant; with the lock, the second waits, re-reads the store and finds the first one's new token.

    Waiting polls, so the event loop stays free. A wait that runs out raises `RefreshBusy`: going ahead would send
    a refresh token the other process may already have spent, and Epiphan would refuse it for good (#162). That
    beat fails and the next one tries again. The kernel drops the lock with its process, so none goes stale."""

    def __init__(self, path: Path):
        self.path = path
        self._fd: int | None = None

    async def acquire(self, timeout_s: float | None = None) -> bool:
        """True once held. False if the lock file can't be opened (the caller carries on: no process can hold it
        then). Raises `RefreshBusy` if another process holds it longer than `timeout_s` (default LOCK_WAIT_S)."""
        if timeout_s is None:
            timeout_s = LOCK_WAIT_S
        try:
            _private_dir(self.path.parent)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError as e:
            log.warning("Can't open the token refresh lock %s (%s); refreshing without it", self.path, e)
            return False
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise RefreshBusy(
                        f"Another Fleetwatch process has held the token refresh lock {self.path} for {timeout_s}s; "
                        "not refreshing now, the next heartbeat tries again"
                    )
                await asyncio.sleep(0.2)
            else:
                self._fd = fd
                return True

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


@runtime_checkable
class TokenStore(TokenStorage, Protocol):
    where: str  # for people: a path, or which keychain

    def has_tokens(self) -> bool: ...

    def expires_at(self) -> datetime | None: ...

    def granted_scope(self) -> str | None: ...

    def oauth_metadata(self) -> dict | None: ...

    def set_oauth_metadata(self, oauth: dict) -> None: ...

    def is_dead(self) -> bool: ...

    def mark_dead(self) -> None: ...

    def clear(self) -> None: ...

    def refresh_lock(self) -> RefreshLock | None: ...


class _JsonStore(TokenStorage):
    """The SDK methods over one JSON dict; subclasses supply `_load`, `_save` and `_drop`."""

    legacy: Path | None = None  # the old token file, moved in on first use
    lock_path: Path | None = None  # `<token file>.lock`: the refresh lock all stores on this machine share

    def _load(self) -> dict:
        raise NotImplementedError

    def _save(self, data: dict) -> None:
        raise NotImplementedError

    def _drop(self) -> None:
        raise NotImplementedError

    def _read(self, migrate: bool = True) -> dict:
        data = self._load()
        if self.legacy is None:
            return data
        if not data:
            data = FileTokenStorage(self.legacy)._load()
            if data and migrate:
                self._save(data)
                self.legacy.unlink(missing_ok=True)
        elif migrate and data.get("tokens"):
            # This store holds a token, so a plain file left behind (a backup restored, an older build run once)
            # is stale and never read again. Remove it; this store, not the file, is the one that gets refreshed.
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
        data.pop("dead", None)  # a new token (sign-in or refresh) means the sign-in works again
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

    def oauth_metadata(self) -> dict | None:
        """The OAuth endpoints discovered at sign-in (no secrets), so a refresh after a restart goes to the real
        token endpoint. None before the first sign-in that saved them."""
        raw = self._read(migrate=False).get("oauth")
        return raw if isinstance(raw, dict) else None

    def set_oauth_metadata(self, oauth: dict) -> None:
        data = self._read()
        if data.get("oauth") != oauth:
            data["oauth"] = oauth
            self._save(data)

    def granted_scope(self) -> str | None:
        """The scope Epiphan granted with the stored token, if it said. Only looks; never migrates."""
        scope = (self._read(migrate=False).get("tokens") or {}).get("scope")
        return scope if isinstance(scope, str) and scope.strip() else None

    def is_dead(self) -> bool:
        """True when Epiphan refused the refresh token: only a new `fleetwatch login` helps."""
        return bool(self._read(migrate=False).get("dead"))

    def mark_dead(self) -> None:
        data = self._read()
        data["dead"] = True
        self._save(data)

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

    def refresh_lock(self) -> RefreshLock | None:
        """A fresh, unheld lock on this machine's token refresh; None when the store has no folder to put it in."""
        return RefreshLock(self.lock_path) if self.lock_path is not None else None


class FileTokenStorage(_JsonStore):
    """One JSON file, mode 0600, holding the token and the registered client."""

    def __init__(self, path: Path):
        self.path = path
        self.lock_path = path.with_suffix(".lock")
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
        self.lock_path = legacy.with_suffix(".lock") if legacy is not None else None
        self._keychain = [keychain] if keychain else []  # a test keychain file; normally the default one
        self.where = f"macOS Keychain (service {service})"

    def _find(self, account: str) -> str | None:
        """One item's secret, or None if there is no such item."""
        r = _run(["security", "find-generic-password", "-s", self.service, "-a", account, "-w", *self._keychain])
        if r.returncode == NOT_FOUND:
            return None
        if r.returncode:
            raise TokenStoreError(f"Keychain: {r.stderr.strip() or f'security exited {r.returncode}'}")
        return r.stdout.rstrip("\n")

    def _index(self) -> dict | None:
        """The `index` item: which generation of parts holds the current JSON. None in an older layout."""
        raw = self._find(INDEX)
        if raw is None:
            return None
        try:
            index = json.loads(raw)
        except ValueError:
            return None
        ok = isinstance(index, dict) and isinstance(index.get("gen"), str) and isinstance(index.get("parts"), int)
        return index if ok else None

    def _load(self) -> dict:
        """The JSON a save left whole. A save writes a new generation of parts and only then points the index at
        it (#121), so a reader sees the old JSON or the new one. A reader that lands as the writer removes the old
        generation reads the index again."""
        for _ in range(READ_ATTEMPTS):
            index = self._index()
            if index is None:
                return self._load_legacy()
            parts = [self._find(f"{index['gen']}-{i}") for i in range(index["parts"])]
            if None in parts:
                continue  # a newer save removed this generation while we read it
            try:
                return json.loads("".join(parts))
            except ValueError:
                continue
        raise TokenStoreError("Keychain: the saved sign-in kept changing while it was read; try again")

    def _load_legacy(self) -> dict:
        """The layout before #121: `part-0`, `part-1`, ... written in place. Read once; the next save replaces it."""
        parts = []
        for i in itertools.count():
            part = self._find(f"part-{i}")
            if part is None:
                break
            parts.append(part)
        try:
            return json.loads("".join(parts)) if parts else {}
        except ValueError:  # half-written by an older build: treat as signed out
            return {}

    def _add(self, items: dict[str, str]) -> int:
        """Write items through `security -i` on stdin (hex), so no secret is in argv. Returns the exit code."""
        kc = " ".join(self._keychain)
        script = "".join(
            f"add-generic-password -U -s {self.service} -a {account} -X {value.encode().hex()} {kc}\n"
            for account, value in items.items()
        )
        return _run(["security", "-i"], stdin=script).returncode

    def _save(self, data: dict) -> None:
        text = json.dumps(data)  # ASCII only, so a chunk boundary never splits a character
        chunks = [text[i : i + CHUNK] for i in range(0, len(text), CHUNK)]
        old = self._index()
        gen = f"g-{secrets.token_hex(4)}"
        if self._add({f"{gen}-{i}": c for i, c in enumerate(chunks)}):
            self._delete_gen(gen, len(chunks))  # best effort: the index still names the old, whole generation
            raise TokenStoreError("Keychain: couldn't save the token (security refused it)")
        if self._add({INDEX: json.dumps({"gen": gen, "parts": len(chunks)})}):
            self._delete_gen(gen, len(chunks))
            raise TokenStoreError("Keychain: couldn't save the token (security refused the index)")
        if old is not None:
            self._delete_gen(old["gen"], old["parts"])
        else:
            self._delete_prefix("part-")  # the layout before #121, now replaced

    def _delete(self, account: str) -> bool:
        """Delete one item. False if it wasn't there."""
        r = _run(["security", "delete-generic-password", "-s", self.service, "-a", account, *self._keychain])
        if r.returncode == NOT_FOUND:
            return False
        if r.returncode:
            raise TokenStoreError(f"Keychain: {r.stderr.strip() or f'security exited {r.returncode}'}")
        return True

    def _delete_gen(self, gen: str, parts: int) -> None:
        for i in range(parts):
            self._delete(f"{gen}-{i}")

    def _delete_prefix(self, prefix: str) -> None:
        for i in itertools.count():
            if not self._delete(f"{prefix}{i}"):
                return

    def _drop(self) -> None:
        index = self._index()
        if index is not None:
            self._delete_gen(index["gen"], index["parts"])
        self._delete(INDEX)
        self._delete_prefix("part-")


class SystemdCredsTokenStorage(_JsonStore):
    """A file encrypted with `systemd-creds --user`: only this user on this machine can decrypt it."""

    def __init__(self, path: Path, legacy: Path | None = None, name: str = SERVICE):
        self.path, self.legacy, self.name = path, legacy, name
        self.lock_path = path.with_suffix(".lock")
        self.where = f"{path} (systemd-creds, encrypted)"

    def _creds(self, verb: str, src: str, dst: str, stdin: str | None = None) -> str:
        r = _run(["systemd-creds", verb, "--user", f"--name={self.name}", src, dst], stdin=stdin)
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


def make_token_store(kind: str, token_file: Path, service: str = SERVICE) -> TokenStore:
    """Build the store `FLEETWATCH_TOKEN_STORE` names. `token_file` is the file store, and the file others move from.
    `service` names the Keychain item and the systemd-creds credential: the sandbox slot passes SANDBOX_SERVICE, so
    the two sign-ins never share a Keychain entry."""
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
        return KeychainTokenStorage(service=service, legacy=token_file)
    if kind == "systemd-creds":
        if not has_creds:
            raise ValueError("FLEETWATCH_TOKEN_STORE=systemd-creds needs Linux with systemd 256 or later")
        return SystemdCredsTokenStorage(token_file.with_suffix(".cred"), legacy=token_file, name=service)
    return FileTokenStorage(token_file)
