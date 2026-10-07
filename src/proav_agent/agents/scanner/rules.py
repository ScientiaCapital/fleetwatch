"""Fleet scan: what needs attention. Plain rules, no LLM. Ported from the Edge Claude Kit's /find-problems.

Priorities are words, never codes. Storage warnings are an FYI, never a problem: Pearls on a CMS record
locally and upload after class, so a full disk is routine.
"""

from datetime import timedelta

from proav_agent.epiphan.parse import STORAGE_WARNINGS
from proav_agent.model import Device, Finding, Fleet, Priority
from proav_agent.policy import Policy

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


def _in_scope(d: Device, policy: Policy) -> bool:
    if d.name in policy.exclude_devices:
        return False
    return not policy.groups or d.group in policy.groups


def scan(fleet: Fleet, policy: Policy) -> list[Finding]:
    findings: list[Finding] = []
    newest = newest_firmware(fleet)
    storage_count = 0
    for d in sorted(fleet.devices.values(), key=lambda x: x.name.lower()):
        if not _in_scope(d, policy):
            continue
        if any(w in STORAGE_WARNINGS for w in d.warnings):
            storage_count += 1

        if not d.online:
            recording = [c.name for c in d.channels.values() if c.recording]
            if recording:
                findings.append(
                    Finding(
                        key=f"{d.id}:offline-recording",
                        priority=Priority.FIX_FIRST,
                        device_id=d.id,
                        device_name=d.name,
                        what=f"{d.name} is offline but still shows {', '.join(recording)} recording",
                        impact="Edge can't see or stop that recording, and it won't upload until the unit is back",
                        fix="Check power and network at the unit",
                    )
                )
            else:
                findings.append(
                    Finding(
                        key=f"{d.id}:offline",
                        priority=Priority.FIX_FIRST,
                        device_id=d.id,
                        device_name=d.name,
                        what=f"{d.name} is offline",
                        impact="Classes in that room won't record or stream until it's back",
                        fix="Check power and the network cable at the unit",
                    )
                )
            continue

        for c in d.channels.values():
            if "channel_no_signal" in c.warnings:
                findings.append(
                    Finding(
                        key=f"{d.id}:{c.id}:no-signal",
                        priority=Priority.FIX_FIRST,
                        device_id=d.id,
                        device_name=d.name,
                        what=f"No picture on {c.name} in {d.name}",
                        impact="The next class on that channel records a blank screen",
                        fix="Check the camera or the HDMI/SDI cable feeding it",
                    )
                )
        if "source_no_signal" in d.warnings and not any("channel_no_signal" in c.warnings for c in d.channels.values()):
            findings.append(
                Finding(
                    key=f"{d.id}:idle-input",
                    priority=Priority.WHEN_CONVENIENT,
                    device_id=d.id,
                    device_name=d.name,
                    what=f"An input on {d.name} has no signal",
                    impact="Nothing, if that input isn't used by a channel",
                    fix="Unplug it or tidy the cable when you're next in the room",
                )
            )

        fam = firmware_family(d.model)
        if d.firmware and fam in newest and _version(d.firmware) < _version(newest[fam]):
            findings.append(
                Finding(
                    key=f"{d.id}:firmware",
                    priority=Priority.FIX_SOON,
                    device_id=d.id,
                    device_name=d.name,
                    what=f"{d.name} runs firmware {d.firmware}; others like it run {newest[fam]}",
                    impact="Works fine today; keeps the fleet consistent",
                    fix="Update firmware when the room is free",
                )
            )

        sysinfo = fleet.system.get(d.id)
        if sysinfo:
            t = policy.thresholds
            if sysinfo.cpu_load_pct is not None and sysinfo.cpu_load_pct >= t.cpu_load_pct:
                findings.append(
                    Finding(
                        key=f"{d.id}:cpu",
                        priority=Priority.FIX_SOON,
                        device_id=d.id,
                        device_name=d.name,
                        what=f"{d.name} is working hard (CPU at {sysinfo.cpu_load_pct:.0f}%)",
                        impact="Streams may stutter if it stays this high",
                        fix="Check for an unused channel or a layout that's too heavy",
                    )
                )
            if sysinfo.cpu_temp_c is not None and sysinfo.cpu_temp_c >= t.cpu_temp_c:
                findings.append(
                    Finding(
                        key=f"{d.id}:temp",
                        priority=Priority.FIX_SOON,
                        device_id=d.id,
                        device_name=d.name,
                        what=f"{d.name} is running warm ({sysinfo.cpu_temp_c:.0f}°C)",
                        impact="It may slow down to cool off",
                        fix="Check the vents and airflow around it",
                    )
                )
            if sysinfo.up_since and fleet.taken_at - sysinfo.up_since <= timedelta(minutes=t.recent_reboot_minutes):
                mins = int((fleet.taken_at - sysinfo.up_since).total_seconds() // 60)
                findings.append(
                    Finding(
                        key=f"{d.id}:reboot:{sysinfo.up_since.isoformat()[:16]}",
                        priority=Priority.FIX_SOON,
                        device_id=d.id,
                        device_name=d.name,
                        what=f"{d.name} restarted about {mins} minutes ago",
                        impact="Anything that was recording then stopped",
                        fix="Check it came back with the right settings",
                    )
                )

    if storage_count:
        findings.append(
            Finding(
                key="fleet:storage",
                priority=Priority.WHEN_CONVENIENT,
                device_id="",
                device_name="",
                what=f"FYI: {storage_count} {'Pearl has' if storage_count == 1 else 'Pearls have'} little or no local space left. "
                "That's normal when recordings upload to your CMS.",
                fyi=True,
            )
        )
    return findings
