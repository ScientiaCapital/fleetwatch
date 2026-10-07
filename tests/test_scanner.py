from datetime import timedelta

from fleetwatch.agents.scanner.rules import firmware_family, newest_firmware, scan
from fleetwatch.model import Channel, Device, Fleet, Priority, SystemStatus
from fleetwatch.policy import Policy, Thresholds
from tests.conftest import NOW


def test_storage_is_fyi_not_problem(fleet, policy):
    findings = scan(fleet, policy)
    storage = [f for f in findings if "storage" in f.key]
    assert len(storage) == 1 and storage[0].fyi and storage[0].what.startswith("FYI:")
    assert not any("disk_space_error" in f.what or "no_storage_detected" in f.what for f in findings)


def test_priorities_are_words_and_calm(fleet, policy):
    for f in scan(fleet, policy):
        assert f.priority in Priority
        low = (f.what + f.impact + f.fix).lower()
        for banned in ("critical", "urgent", "at risk", "danger", "failure", "p1", "p2", "p3", "—"):
            assert banned not in low, f"{banned!r} in {f.what!r}"


def test_no_signal_is_fix_first(fleet, policy):
    findings = scan(fleet, policy)
    ns = [f for f in findings if f.key.endswith(":no-signal")]
    assert ns and all(f.priority is Priority.FIX_FIRST and "No picture on" in f.what for f in ns)


def test_offline_and_offline_while_recording(policy):
    fleet = Fleet(taken_at=NOW)
    fleet.devices["a"] = Device(id="a", name="Room A", online=False)
    fleet.devices["b"] = Device(
        id="b", name="Room B", online=False, channels={"1": Channel("1", "Program", recording=True)}
    )
    keys = {f.key: f for f in scan(fleet, policy)}
    assert keys["a:offline"].priority is Priority.FIX_FIRST
    assert "still shows Program recording" in keys["b:offline-recording"].what


def test_firmware_family_rule():
    assert firmware_family("Pearl Mini") == firmware_family("Pearl-2") == firmware_family("Pearl Nexus") == "pearl"
    assert firmware_family("EC20") != "pearl"
    fleet = Fleet(taken_at=NOW)
    fleet.devices["m"] = Device(id="m", name="Mini", model="Pearl Mini", firmware="4.24.5")
    fleet.devices["n"] = Device(id="n", name="Nano", model="Pearl Nano", firmware="4.24.6")
    fleet.devices["c"] = Device(id="c", name="Cam", model="EC20", firmware="3.6.44")
    assert newest_firmware(fleet) == {"pearl": "4.24.6", "ec20": "3.6.44"}
    keys = {f.key for f in scan(fleet, Policy())}
    assert "m:firmware" in keys and "c:firmware" not in keys and "n:firmware" not in keys


def test_system_thresholds_and_recent_reboot():
    fleet = Fleet(taken_at=NOW)
    fleet.devices["x"] = Device(id="x", name="X", model="Pearl Nano")
    fleet.system["x"] = SystemStatus(cpu_load_pct=95, cpu_temp_c=85, up_since=NOW - timedelta(minutes=10))
    keys = {f.key.split(":")[1] for f in scan(fleet, Policy(thresholds=Thresholds()))}
    assert {"cpu", "temp", "reboot"} <= keys
    fleet.system["x"] = SystemStatus(cpu_load_pct=20, cpu_temp_c=50, up_since=NOW - timedelta(days=3))
    assert not scan(fleet, Policy())


def test_scope_filters(fleet):
    only = Policy(groups=("Higher Ed",))
    assert all(fleet.devices[f.device_id].group == "Higher Ed" for f in scan(fleet, only) if f.device_id)
    name = next(d.name for d in fleet.devices.values() if not d.online)
    assert not any(f.device_name == name for f in scan(fleet, Policy(exclude_devices=(name,))))
