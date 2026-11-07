"""`fleetwatch doctor`: is this machine ready to watch the fleet? Read-only, no sign-in, no Epiphan tool calls.

Each check is OK, WARN (works, but worth a look) or FAIL (fix before relying on it). Exit code 1 on any FAIL.

Row names and details follow `language` in policy.yaml (`lang`, from i18n.py); OK/WARN/FAIL/INFO never change. An
exception's text (`str(e)`) and a stored failure reason pass through untranslated: they are what the library or
the heartbeat said, and the person may need to search for them as written.
"""

import logging
import os
import platform
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from urllib.parse import urlparse

import httpx

from fleetwatch.config import Settings, reveal
from fleetwatch.epiphan.token_store import (
    FileTokenStorage,
    KeychainTokenStorage,
    TokenStoreError,
    make_token_store,
)
from fleetwatch.i18n import t
from fleetwatch.policy import KNOWN_WRITE_TOOLS, load_policy, load_tools
from fleetwatch.redact import redact
from fleetwatch.runner import LAUNCHD_LABEL
from fleetwatch.state import MAX_PENDING, PROPOSALS_PER_HOUR, State

OK, WARN, FAIL = "OK", "WARN", "FAIL"
INFO = "INFO"  # always worth knowing, never something to fix: not counted in the summary


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _name(lang: str, row: str) -> str:
    return t(lang, f"doctor.name.{row}")


def _say(lang: str, msg: str, **kw: Any) -> str:
    return t(lang, f"doctor.{msg}", **kw)


def _version(lang: str = "en") -> Check:
    try:
        v = version("fleetwatch")
    except PackageNotFoundError:
        v = _say(lang, "version.unknown")
    detail = _say(lang, "version", v=v, py=platform.python_version(), machine=platform.machine())
    return Check(_name(lang, "version"), OK, detail)


def _policy(s: Settings, lang: str = "en") -> Check:
    name = _name(lang, "policy")
    try:
        p = load_policy(s.policy_file)
    except Exception as e:  # noqa: BLE001  (any load error is the check's FAIL detail)
        return Check(name, FAIL, f"{s.policy_file}: {e}")
    mode = _say(lang, "policy.propose" if p.proposes else "policy.observe")
    return Check(name, OK, _say(lang, "policy.ok", mode=mode, n=p.heartbeat_seconds))


def _guard(s: Settings, lang: str = "en") -> Check:
    name = _name(lang, "guard")
    try:
        tools = load_tools(s.tool_policy_file)
    except Exception as e:  # noqa: BLE001  (any load error is the check's FAIL detail)
        return Check(name, FAIL, str(e))  # the message names the file
    leaked = sorted(t for t in KNOWN_WRITE_TOOLS | tools.write | tools.disruptive if tools.is_read(t))
    if leaked:
        return Check(name, FAIL, _say(lang, "guard.leaked", tools=", ".join(leaked)))
    return Check(name, OK, _say(lang, "guard.ok", n=len(tools.read)))


def _redaction(lang: str = "en") -> Check:
    secret = "live_sk_doctor_selftest_123"
    out = redact(f"rtmp://ingest.example.com/app/{secret} streaming_key={secret}")
    if secret in str(out):
        return Check(_name(lang, "redaction"), FAIL, _say(lang, "redaction.fail"))
    return Check(_name(lang, "redaction"), OK, _say(lang, "redaction.ok"))


def _sign_in(s: Settings, lang: str = "en") -> Check:
    name = _name(lang, "sign_in")
    if reveal(s.epiphan_token):
        return Check(name, OK, _say(lang, "sign_in.static"))
    try:
        store = make_token_store(s.token_store, s.token_file)
        signed_in = store.has_tokens()
    except (TokenStoreError, ValueError) as e:
        return Check(name, FAIL, str(e))
    if not signed_in:
        return Check(name, WARN, _say(lang, "sign_in.none"))
    if not isinstance(store, FileTokenStorage):  # the file-mode check below is for the plain file only
        return Check(name, OK, _say(lang, "sign_in.store", where=store.where))
    f = s.token_file
    mode = stat.S_IMODE(os.stat(f).st_mode)
    if mode & 0o077:
        return Check(name, FAIL, _say(lang, "sign_in.readable", f=f, mode=f"{mode:o}"))
    return Check(name, OK, _say(lang, "sign_in.file", f=f))


def _write_capable(s: Settings, lang: str = "en") -> Check | None:
    """Epiphan Edge has no read-only OAuth scope: whatever signed in can make changes if the account can. The guard
    keeps Fleetwatch from writing; this row says what a stolen token could do. Offline, never shows the token."""
    name, advice = _name(lang, "write_capable"), _say(lang, "write_capable")
    if reveal(s.epiphan_token):
        return Check(name, INFO, advice)
    try:
        store = make_token_store(s.token_store, s.token_file)
        if not store.has_tokens():
            return None
        scope = getattr(store, "granted_scope", lambda: None)()
    except (TokenStoreError, ValueError):
        return None  # the Sign-in row already reports it
    return Check(name, INFO, _say(lang, "write_capable.scope", advice=advice, scope=scope) if scope else advice)


def _issuer(s: Settings, lang: str = "en") -> Check | None:
    """The issuer saved at sign-in and whether Epiphan says its redirect sends `iss` (RFC 9207, #13). Offline: read
    from the endpoints `login` saved, which hold no secrets. None without a stored sign-in."""
    if reveal(s.epiphan_token):
        return None
    try:
        store = make_token_store(s.token_store, s.token_file)
        if not store.has_tokens():
            return None
        oauth = getattr(store, "oauth_metadata", lambda: None)() or {}
    except (TokenStoreError, ValueError):
        return None  # the Sign-in row already reports it
    meta = oauth.get("metadata") if isinstance(oauth.get("metadata"), dict) else {}
    issuer = meta.get("issuer") if isinstance(meta.get("issuer"), str) else None
    sends = {True: "yes", False: "no"}.get(meta.get("authorization_response_iss_parameter_supported"), "unstated")
    name = _name(lang, "issuer")
    if issuer is None:
        return Check(name, INFO, _say(lang, "issuer.none"))
    return Check(name, INFO, _say(lang, f"issuer.{sends}", issuer=redact(issuer)))


def _keychain_access(s: Settings, lang: str = "en") -> Check | None:
    """The Keychain items trust /usr/bin/security, so any program running as this macOS user can read them without
    a prompt. Only the operating-system user keeps other software out (SECURITY.md, Trust model)."""
    if reveal(s.epiphan_token):
        return None
    try:
        store = make_token_store(s.token_store, s.token_file)
        if not isinstance(store, KeychainTokenStorage) or not store.has_tokens():
            return None
    except (TokenStoreError, ValueError):
        return None  # the Sign-in row already reports it
    return Check(_name(lang, "keychain"), INFO, _say(lang, "keychain"))


def _token_expiry(s: Settings, now: datetime | None = None, lang: str = "en") -> Check | None:
    """When the stored access token runs out, read offline from the store. Never shows the token itself."""
    if reveal(s.epiphan_token):
        return None
    try:
        store = make_token_store(s.token_store, s.token_file)
        if not store.has_tokens():
            return None
        when = getattr(store, "expires_at", lambda: None)()  # a store without it just shows no expiry
        dead = getattr(store, "is_dead", lambda: False)()
    except (TokenStoreError, ValueError):
        return None  # the Sign-in row already reports it
    name = _name(lang, "token_expiry")
    if dead:
        return Check(name, FAIL, _say(lang, "expiry.dead"))
    if when is None:
        return Check(name, OK, _say(lang, "expiry.unknown"))
    now = now or datetime.now(UTC)
    local = when.astimezone().strftime("%Y-%m-%d %H:%M")
    if when <= now:
        return Check(name, OK, _say(lang, "expiry.expired", when=local))
    return Check(name, OK, _say(lang, "expiry.ok", when=local))


def _sandbox(s: Settings, lang: str = "en") -> Check:
    """The v0.2 sandbox sign-in, in its own slot: the only sign-in an approved change may run on. Offline; never
    shows the token. INFO either way: with none, nothing can change, which is what v0.1 wants."""
    from fleetwatch.epiphan.executor import sandbox_store

    name = _name(lang, "sandbox")
    try:
        store = sandbox_store(s)
        signed_in = store.has_tokens()
    except ValueError as e:  # the slot is the normal sign-in's file, or the store kind is wrong
        return Check(name, FAIL, str(e))
    except TokenStoreError as e:
        return Check(name, WARN, str(e))
    if not signed_in:
        return Check(name, INFO, _say(lang, "sandbox.none"))
    if isinstance(store, FileTokenStorage) and stat.S_IMODE(os.stat(store.path).st_mode) & 0o077:
        return Check(name, FAIL, _say(lang, "sandbox.readable", f=store.path))
    return Check(name, INFO, _say(lang, "sandbox.ok", where=store.where))


def _fence(s: Settings, lang: str = "en") -> Check:
    """v0.2: with autonomy: propose, a change needs a fence (docs/design/approved-writes.md, "Fence"). Offline."""
    from fleetwatch.fence import Fence

    name = _name(lang, "fence")
    try:
        p = load_policy(s.policy_file)
    except (OSError, ValueError):
        return Check(name, WARN, _say(lang, "fence.no_policy"))
    if not p.proposes:
        return Check(name, INFO, _say(lang, "fence.observe"))
    fence = Fence.from_settings(s)
    if fence.invalid:
        shown = ", ".join(repr(e[:40]) for e in fence.invalid[:3])
        n = len(fence.invalid)
        return Check(name, FAIL, _say(lang, "fence.invalid.one" if n == 1 else "fence.invalid.many", n=n, shown=shown))
    if not fence.is_set:
        return Check(name, FAIL, _say(lang, "fence.unset"))
    if not fence.device_ids:
        return Check(name, WARN, _say(lang, "fence.team_only"))
    n = len(fence.device_ids)
    detail = _say(lang, "fence.ok.one" if n == 1 else "fence.ok.many", n=n)
    return Check(name, OK, detail + (_say(lang, "fence.team_too") if fence.team_id else ""))


def _state_dir(s: Settings, lang: str = "en") -> Check:
    d = s.state_db.parent
    name = _name(lang, "state_dir")
    if d.exists() and not os.access(d, os.W_OK):
        return Check(name, FAIL, _say(lang, "state_dir.unwritable", d=d))
    return Check(name, OK, f"{d}" if d.exists() else _say(lang, "state_dir.new", d=d))


def _last_beat(s: Settings, lang: str = "en") -> Check | None:
    """Offline: the last failed heartbeat and its reason (redacted when it was recorded), if none has worked since."""
    if not s.state_db.exists():
        return None
    try:
        state = State(s.state_db)
        try:
            failed = state.last_failed_beat()
        finally:
            state.db.close()
    except Exception:  # noqa: BLE001  (the Audit log row reports an unreadable file)
        return None
    if failed is None:
        return None
    at, reason = failed
    return Check(
        _name(lang, "last_beat"), WARN, _say(lang, "last_beat", when=f"{at.astimezone():%Y-%m-%d %H:%M}", reason=reason)
    )


def _audit_log(s: Settings, lang: str = "en") -> Check:
    """Offline: walks the audit hash chain. Names the first broken row's ID and nothing from its content."""
    name = _name(lang, "audit")
    if not s.state_db.exists():
        return Check(name, OK, _say(lang, "audit.none"))
    try:
        state = State(s.state_db)
        try:
            bad = state.verify_audit()
        finally:
            state.db.close()
    except Exception as e:  # noqa: BLE001  (a locked or unreadable file is a finding, not a crash)
        return Check(name, FAIL, _say(lang, "audit.unreadable", error=type(e).__name__))
    if bad is None:
        return Check(name, OK, _say(lang, "audit.ok"))
    return Check(name, FAIL, _say(lang, "audit.bad", row=bad))


def _proposal_queue(s: Settings, lang: str = "en") -> Check:
    """Offline: how full the v0.2 proposal queue is, from the same limits State enforces. Warns when the assistant
    can't add a proposal, so a refused one isn't a mystery."""
    name = _name(lang, "queue")
    pending, hour, max_pending, max_hour = 0, 0, MAX_PENDING, PROPOSALS_PER_HOUR
    if s.state_db.exists():
        try:
            state = State(s.state_db)
            try:
                pending, hour, max_pending, max_hour = state.queue_status()
            finally:
                state.db.close()
        except Exception as e:  # noqa: BLE001  (a locked or unreadable file is a finding, not a crash)
            return Check(name, WARN, _say(lang, "queue.unreadable", error=type(e).__name__))
    counts = _say(lang, "queue.counts", pending=pending, max_pending=max_pending, hour=hour, max_hour=max_hour)
    if pending >= max_pending:
        return Check(name, WARN, _say(lang, "queue.full_pending", counts=counts))
    if hour >= max_hour:
        return Check(name, WARN, _say(lang, "queue.full_hour", counts=counts))
    return Check(name, INFO, counts)


def _reach(name: str, url: str, reach: Callable[[str], bool], lang: str = "en") -> Check:
    host = urlparse(url).netloc or url
    if reach(url):
        return Check(name, OK, host)
    return Check(name, WARN, _say(lang, "reach.fail", host=host))


def _mask(key: str, lang: str = "en") -> str:
    """Enough of a key to tell two apart, never enough to use: its prefix and last four characters."""
    return f"{key[:7]}…{key[-4:]}" if len(key) >= 20 else _say(lang, "assistant.key_set")


def _assistant(s: Settings, lang: str = "en") -> Check:
    """Whether `fleetwatch ask` sends the question and redacted fleet data to Anthropic. Offline: no API call."""
    key = reveal(s.anthropic_api_key)
    if not key:
        return Check(_name(lang, "assistant"), INFO, _say(lang, "assistant.off"))
    return Check(_name(lang, "assistant"), INFO, _say(lang, "assistant.on", model=s.ai_model, mask=_mask(key, lang)))


def _slack(s: Settings, lang: str = "en") -> Check:
    if reveal(s.slack_bot_token):
        return Check("Slack", OK, _say(lang, "slack.posts", channel=s.slack_channel))
    return Check("Slack", OK, _say(lang, "slack.console"))  # "Slack" and "Teams" are names, the same in Spanish


def _teams(s: Settings, lang: str = "en") -> Check:
    """Never shows or calls the webhook: the URL is the credential."""
    url = s.teams_webhook_url.get_secret_value() if s.teams_webhook_url else ""
    if not url:
        return Check("Teams", OK, _say(lang, "teams.none"))
    if not url.startswith("https://"):
        return Check("Teams", WARN, _say(lang, "teams.https"))
    return Check("Teams", OK, _say(lang, "teams.ok"))


def _slack_commands(s: Settings, lang: str = "en") -> Check:
    """/fleetwatch over Socket Mode. Offline: checks the token's shape (never prints it) and the allowlist."""
    name = _name(lang, "slack_commands")
    if not reveal(s.slack_app_token):
        return Check(name, OK, _say(lang, "slack_commands.off"))
    if not reveal(s.slack_app_token).startswith("xapp-"):
        return Check(name, FAIL, _say(lang, "slack_commands.xapp"))
    try:
        p = load_policy(s.policy_file)
    except Exception:  # noqa: BLE001  (the Policy row already reports it)
        return Check(name, WARN, _say(lang, "slack_commands.no_policy"))
    if p.slack_allowed_usergroup and not reveal(s.slack_bot_token):
        return Check(name, WARN, _say(lang, "slack_commands.group_needs_bot"))
    if not p.slack_allowed_user_ids and not p.slack_allowed_usergroup:
        return Check(name, WARN, _say(lang, "slack_commands.nobody"))
    n = len(p.slack_allowed_user_ids)
    key = ("one" if n == 1 else "many") + ("_group" if p.slack_allowed_usergroup else "")
    return Check(name, OK, _say(lang, f"slack_commands.{key}", n=n))


def can_reach(url: str) -> bool:
    """Any HTTP answer counts: we only want DNS, TLS and a route. Nothing is sent but a HEAD request."""
    try:
        httpx.head(url, timeout=5, follow_redirects=False)
        return True
    except httpx.HTTPError:
        return False


def _sign_in_dead(s: Settings) -> bool:
    if reveal(s.epiphan_token):
        return False
    try:
        return bool(getattr(make_token_store(s.token_store, s.token_file), "is_dead", lambda: False)())
    except (TokenStoreError, ValueError):
        return False  # the Sign-in row already reports it


def _restart_command() -> str:
    if platform.system() == "Darwin":
        return f"launchctl kickstart gui/$(id -u)/{LAUNCHD_LABEL}"
    return "systemctl --user restart fleetwatch"


def service_status(lang: str = "en") -> tuple[str, str]:
    system = platform.system()
    if system == "Darwin" and shutil.which("launchctl"):
        r = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"], capture_output=True, text=True, check=False
        )
        if r.returncode != 0:
            return WARN, _say(lang, "service.launchd_missing")
        if "state = running" in r.stdout:
            return OK, _say(lang, "service.launchd_running")
        return WARN, _say(lang, "service.launchd_stopped")
    if system == "Linux" and shutil.which("systemctl"):
        r = subprocess.run(
            ["systemctl", "--user", "is-active", "fleetwatch"], capture_output=True, text=True, check=False
        )
        state = r.stdout.strip() or "unknown"
        if state == "active":
            return OK, _say(lang, "service.systemd_active")
        return WARN, _say(lang, "service.systemd_other", state=state)
    if os.path.exists("/.dockerenv"):
        return OK, _say(lang, "service.container")
    return WARN, _say(lang, "service.none", system=system)


def run_checks(
    s: Settings,
    *,
    reach: Callable[[str], bool] = can_reach,
    service: Callable[[], tuple[str, str]] | None = None,
    lang: str = "en",
) -> list[Check]:
    for name in ("httpx", "httpx2"):  # can_reach uses httpx; the MCP SDK uses its httpx2 fork
        logging.getLogger(name).setLevel(logging.WARNING)  # keep the report to one line per check
    checks = [
        _version(lang),
        _policy(s, lang),
        _guard(s, lang),
        _redaction(lang),
        _sign_in(s, lang),
        *filter(
            None,
            [_write_capable(s, lang), _issuer(s, lang), _keychain_access(s, lang), _token_expiry(s, lang=lang)],
        ),
        _sandbox(s, lang),
        _fence(s, lang),
        _state_dir(s, lang),
        _audit_log(s, lang),
        *filter(None, [_last_beat(s, lang)]),
        _proposal_queue(s, lang),
        _slack(s, lang),
        _slack_commands(s, lang),
        _teams(s, lang),
        _assistant(s, lang),
    ]
    checks.append(_reach(_name(lang, "epiphan_reach"), s.epiphan_mcp_url, reach, lang))
    if reveal(s.slack_bot_token):
        checks.append(_reach(_name(lang, "slack_reach"), "https://slack.com/api/api.test", reach, lang))
    status, detail = service() if service else service_status(lang)
    if _sign_in_dead(s):  # #122: the agent stopped on purpose; say why and how to start it again
        status, detail = WARN, _say(lang, "service.dead", restart=_restart_command())
    checks.append(Check(_name(lang, "service"), status, detail))
    return checks


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.status == FAIL for c in checks) else 0


def print_report(checks: list[Check], out=None, lang: str = "en") -> None:
    out = out or sys.stdout
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"{c.status:<4}  {c.name:<{width}}  {c.detail}", file=out)
    fails = sum(c.status == FAIL for c in checks)
    warns = sum(c.status == WARN for c in checks)
    if fails:
        summary = _say(lang, "summary.fix_look" if warns else "summary.fix", fails=fails, warns=warns)
    elif warns:
        summary = _say(lang, "summary.look", warns=warns)
    else:
        summary = _say(lang, "summary.ok")
    print(f"\n{summary}", file=out)
