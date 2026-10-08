"""`fleetwatch doctor`: is this machine ready to watch the fleet? Read-only, no sign-in, no Epiphan tool calls.

Each check is OK, WARN (works, but worth a look) or FAIL (fix before relying on it). Exit code 1 on any FAIL.
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
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import urlparse

import httpx

from fleetwatch.config import Settings
from fleetwatch.epiphan.token_store import FileTokenStorage, TokenStoreError, make_token_store
from fleetwatch.policy import load_policy, load_tool_policy
from fleetwatch.redact import redact

OK, WARN, FAIL = "OK", "WARN", "FAIL"
LAUNCHD_LABEL = "dev.fleetwatch.agent"

# Epiphan write tools known today. Checked independently of tool_policy.yaml, so an edit that moves one onto the
# read list is caught here even if it was also removed from the file's write list.
KNOWN_WRITE_TOOLS = frozenset(
    {
        "batch_recording",
        "batch_reboot",
        "batch_firmware_update",
        "apply_team_preset",
        "switch_device_to_cms",
        "start_stream_endpoint",
        "stop_stream_endpoint",
        "create_stream_endpoint",
        "update_stream_endpoint",
        "delete_stream_endpoint",
        "create_cms_event",
        "update_cms_event",
        "delete_cms_event",
        "cms_event_action",
        "confirm_cms_event_on_device",
    }
)


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _version() -> Check:
    try:
        v = version("fleetwatch")
    except PackageNotFoundError:
        v = "unknown"
    return Check("Version", OK, f"fleetwatch {v}, Python {platform.python_version()} on {platform.machine()}")


def _policy(s: Settings) -> Check:
    try:
        p = load_policy(s.policy_file)
    except Exception as e:  # noqa: BLE001
        return Check("Policy", FAIL, f"{s.policy_file}: {e}")
    return Check("Policy", OK, f"observe-only, heartbeat every {p.heartbeat_seconds} s")


def _guard(s: Settings) -> Check:
    try:
        tools = load_tool_policy(s.tool_policy_file)
    except Exception as e:  # noqa: BLE001
        return Check("Read-only guard", FAIL, f"{s.tool_policy_file}: {e}")
    leaked = sorted(t for t in KNOWN_WRITE_TOOLS | tools.write | tools.disruptive if tools.is_read(t))
    if leaked:
        return Check("Read-only guard", FAIL, f"write tools on the read list: {', '.join(leaked)}")
    return Check("Read-only guard", OK, f"{len(tools.read)} read tools allowed; every write tool is refused")


def _redaction() -> Check:
    secret = "live_sk_doctor_selftest_123"
    out = redact(f"rtmp://ingest.example.com/app/{secret} streaming_key={secret}")
    if secret in str(out):
        return Check("Redaction", FAIL, "a test stream key got through redact()")
    return Check("Redaction", OK, "stream keys and credentialed URLs are masked")


def _sign_in(s: Settings) -> Check:
    if s.epiphan_token:
        return Check("Sign-in", OK, "static token from FLEETWATCH_EPIPHAN_TOKEN")
    try:
        store = make_token_store(s.token_store, s.token_file)
        signed_in = store.has_tokens()
    except (TokenStoreError, ValueError) as e:
        return Check("Sign-in", FAIL, str(e))
    if not signed_in:
        return Check("Sign-in", WARN, "not signed in: run  fleetwatch login")
    if not isinstance(store, FileTokenStorage):  # the file-mode check below is for the plain file only
        return Check("Sign-in", OK, f"token in {store.where}")
    f = s.token_file
    mode = stat.S_IMODE(os.stat(f).st_mode)
    if mode & 0o077:
        return Check("Sign-in", FAIL, f"{f} is readable by other users (mode {mode:o}): run  chmod 600 {f}")
    return Check("Sign-in", OK, f"token in {f} (mode 600)")


def _state_dir(s: Settings) -> Check:
    d = s.state_db.parent
    if d.exists() and not os.access(d, os.W_OK):
        return Check("State folder", FAIL, f"{d} is not writable")
    return Check("State folder", OK, f"{d}" + ("" if d.exists() else " (created on first run)"))


def _reach(name: str, url: str, reach: Callable[[str], bool]) -> Check:
    host = urlparse(url).netloc or url
    if reach(url):
        return Check(name, OK, host)
    return Check(name, WARN, f"can't reach {host}: check the network, DNS or a proxy")


def _slack(s: Settings) -> Check:
    return Check("Slack", OK, f"posts to {s.slack_channel}" if s.slack_bot_token else "no token: prints to the console")


def _teams(s: Settings) -> Check:
    """Never shows or calls the webhook: the URL is the credential."""
    url = s.teams_webhook_url.get_secret_value() if s.teams_webhook_url else ""
    if not url:
        return Check("Teams", OK, "not configured")
    if not url.startswith("https://"):
        return Check("Teams", WARN, "FLEETWATCH_TEAMS_WEBHOOK_URL should start with https://")
    return Check("Teams", OK, "configured")


def can_reach(url: str) -> bool:
    """Any HTTP answer counts: we only want DNS, TLS and a route. Nothing is sent but a HEAD request."""
    try:
        httpx.head(url, timeout=5, follow_redirects=False)
        return True
    except httpx.HTTPError:
        return False


def service_status() -> tuple[str, str]:
    system = platform.system()
    if system == "Darwin" and shutil.which("launchctl"):
        r = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"], capture_output=True, text=True, check=False
        )
        if r.returncode != 0:
            return WARN, "launchd agent not installed: run  deploy/install.sh"
        return (
            (OK, "launchd agent running")
            if "state = running" in r.stdout
            else (WARN, "launchd agent loaded, not running")
        )
    if system == "Linux" and shutil.which("systemctl"):
        r = subprocess.run(
            ["systemctl", "--user", "is-active", "fleetwatch"], capture_output=True, text=True, check=False
        )
        state = r.stdout.strip() or "unknown"
        if state == "active":
            return OK, "systemd user unit active"
        return WARN, f"systemd user unit {state}: run  deploy/install.sh  (or check  journalctl --user -u fleetwatch)"
    if os.path.exists("/.dockerenv"):
        return OK, "running in a container"
    return WARN, f"no launchd or systemd here ({system}); run  fleetwatch run  yourself"


def run_checks(
    s: Settings,
    *,
    reach: Callable[[str], bool] = can_reach,
    service: Callable[[], tuple[str, str]] = service_status,
) -> list[Check]:
    logging.getLogger("httpx").setLevel(logging.WARNING)  # keep the report to one line per check
    checks = [_version(), _policy(s), _guard(s), _redaction(), _sign_in(s), _state_dir(s), _slack(s), _teams(s)]
    checks.append(_reach("Epiphan reachable", s.epiphan_mcp_url, reach))
    if s.slack_bot_token:
        checks.append(_reach("Slack reachable", "https://slack.com/api/api.test", reach))
    status, detail = service()
    checks.append(Check("Service", status, detail))
    return checks


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.status == FAIL for c in checks) else 0


def print_report(checks: list[Check], out=None) -> None:
    out = out or sys.stdout
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"{c.status:<4}  {c.name:<{width}}  {c.detail}", file=out)
    fails = sum(c.status == FAIL for c in checks)
    warns = sum(c.status == WARN for c in checks)
    if fails:
        summary = f"{fails} to fix before relying on Fleetwatch" + (f", {warns} to look at." if warns else ".")
    elif warns:
        summary = f"Nothing broken. {warns} to look at."
    else:
        summary = "All good."
    print(f"\n{summary}", file=out)
