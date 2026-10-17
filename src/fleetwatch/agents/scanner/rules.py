"""Fleet scan: what needs attention. Plain rules, no LLM. Ported from the Edge Claude Kit's /find-problems.

Priorities are words, never codes. Storage warnings are an FYI, never a problem: Pearls on a CMS record
locally and upload afterwards, so a full disk is routine.
"""

from datetime import timedelta

from fleetwatch.epiphan.parse import STORAGE_WARNINGS
from fleetwatch.i18n import say
from fleetwatch.model import Device, Finding, Fleet, Priority
from fleetwatch.policy import Policy

PEARL_FAMILY = ("pearl-2", "pearl 2", "pearl mini", "pearl nano", "pearl nexus")


def firmware_family(model: str) -> str:
    m = model.lower()
    return "pearl" if any(p in m for p in PEARL_FAMILY) else m or "unknown"


def _version(v: str) -> tuple[int, ...]:
    out = []
    for part in v.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def newest_firmware(fleet: Fleet) -> dict[str, str]:
    newest: dict[str, str] = {}
    for d in fleet.devices.values():
        if not d.firmware:
            continue
        fam = firmware_family(d.model)
        if fam not in newest or _version(d.firmware) > _version(newest[fam]):
            newest[fam] = d.firmware
    return newest


def in_scope(d: Device, policy: Policy) -> bool:
    if d.name in policy.exclude_devices:
        return False
    return not policy.groups or d.group in policy.groups


def _finding(policy: Policy, d: Device, key: str, priority: Priority, rule: str, **kw: str) -> Finding:
    """One finding for a device. `rule` names its three messages in i18n.py: what, impact and fix."""
    return Finding(
        key=f"{d.id}:{key}",
        priority=priority,
        device_id=d.id,
        device_name=d.name,
        what=say(policy, f"scan.{rule}.what", name=d.name, **kw),
        impact=say(policy, f"scan.{rule}.impact", name=d.name, **kw),
        fix=say(policy, f"scan.{rule}.fix", name=d.name, **kw),
    )


def _offline(d: Device, policy: Policy) -> Finding:
    recording = [c.name for c in d.channels.values() if c.recording]
    if d.is_camera:
        # An EC20 doesn't record or stream by itself. The Pearl channel that uses it raises its own
        # Fix first "No picture" item, so the camera itself is Fix soon.
        return _finding(policy, d, "offline", Priority.FIX_SOON, "camera_offline")
    if recording:
        return _finding(
            policy, d, "offline-recording", Priority.FIX_FIRST, "offline_recording", channels=", ".join(recording)
        )
    return _finding(policy, d, "offline", Priority.FIX_FIRST, "offline")


def _signal(d: Device, policy: Policy) -> list[Finding]:
    """No picture on a channel (Fix first), or an input with no signal that no channel uses (When convenient)."""
    findings = [
        _finding(policy, d, f"{c.id}:no-signal", Priority.FIX_FIRST, "no_signal", channel=c.name)
        for c in d.channels.values()
        if "channel_no_signal" in c.warnings
    ]
    if "source_no_signal" in d.warnings and not findings:
        findings.append(_finding(policy, d, "idle-input", Priority.WHEN_CONVENIENT, "idle_input"))
    return findings


def _firmware(d: Device, newest: dict[str, str], policy: Policy) -> list[Finding]:
    fam = firmware_family(d.model)
    if not (d.firmware and fam in newest and _version(d.firmware) < _version(newest[fam])):
        return []
    return [_finding(policy, d, "firmware", Priority.FIX_SOON, "firmware", firmware=d.firmware, newest=newest[fam])]


def _system(d: Device, fleet: Fleet, policy: Policy) -> list[Finding]:
    """Busy CPU, a hot unit, or a recent restart, from the system status read."""
    sysinfo = fleet.system.get(d.id)
    if not sysinfo:
        return []
    findings: list[Finding] = []
    t = policy.thresholds
    if sysinfo.cpu_load_pct is not None and sysinfo.cpu_load_pct >= t.cpu_load_pct:
        findings.append(_finding(policy, d, "cpu", Priority.FIX_SOON, "cpu", pct=f"{sysinfo.cpu_load_pct:.0f}"))
    if sysinfo.cpu_temp_c is not None and sysinfo.cpu_temp_c >= t.cpu_temp_c:
        findings.append(_finding(policy, d, "temp", Priority.FIX_SOON, "temp", temp=f"{sysinfo.cpu_temp_c:.0f}"))
    if sysinfo.up_since and fleet.taken_at - sysinfo.up_since <= timedelta(minutes=t.recent_reboot_minutes):
        mins = int((fleet.taken_at - sysinfo.up_since).total_seconds() // 60)
        findings.append(
            _finding(
                policy, d, f"reboot:{sysinfo.up_since.isoformat()[:16]}", Priority.FIX_SOON, "reboot", mins=str(mins)
            )
        )
    return findings


def scan(fleet: Fleet, policy: Policy) -> list[Finding]:
    findings: list[Finding] = []
    newest = newest_firmware(fleet)
    storage_count = 0
    for d in sorted(fleet.devices.values(), key=lambda x: x.name.lower()):
        if not in_scope(d, policy):
            continue
        if any(w in STORAGE_WARNINGS for w in d.warnings):
            storage_count += 1
        if not d.online:
            findings.append(_offline(d, policy))
            continue
        findings += _signal(d, policy) + _firmware(d, newest, policy) + _system(d, fleet, policy)

    if storage_count:
        findings.append(
            Finding(
                key="fleet:storage",
                priority=Priority.WHEN_CONVENIENT,
                device_id="",
                device_name="",
                what=say(policy, "scan.storage.one" if storage_count == 1 else "scan.storage.many", n=storage_count),
                fyi=True,
            )
        )
    return findings
