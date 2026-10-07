"""`fleetwatch doctor`: every check runs offline here; network and service probes are injected."""

import json
import os
from pathlib import Path

from fleetwatch.config import Settings
from fleetwatch.doctor import FAIL, OK, WARN, exit_code, run_checks

ROOT = Path(__file__).resolve().parents[1]


def settings(tmp_path: Path, **kw) -> Settings:
    base = {
        "policy_file": ROOT / "policy.yaml",
        "tool_policy_file": ROOT / "tool_policy.yaml",
        "state_db": tmp_path / "state.db",
        "token_file": tmp_path / "epiphan-oauth.json",
        "slack_bot_token": None,
        "epiphan_token": None,
    }
    base.update(kw)
    return Settings(_env_file=None, **base)


def signed_in(tmp_path: Path, mode: int = 0o600) -> None:
    f = tmp_path / "epiphan-oauth.json"
    f.write_text(json.dumps({"tokens": {"access_token": "x", "token_type": "Bearer"}}))
    os.chmod(f, mode)


def run(s: Settings, reach: bool = True, service: tuple[str, str] = (OK, "running")):
    return run_checks(s, reach=lambda url: reach, service=lambda: service)


def by_name(checks):
    return {c.name: c for c in checks}


def test_healthy_setup_passes(tmp_path):
    signed_in(tmp_path)
    checks = run(settings(tmp_path))
    assert not [c for c in checks if c.status != OK], [c for c in checks if c.status != OK]
    assert exit_code(checks) == 0
    names = by_name(checks)
    for expected in ("Version", "Policy", "Read-only guard", "Redaction", "Sign-in", "Epiphan reachable", "Service"):
        assert expected in names


def test_token_readable_by_others_fails(tmp_path):
    signed_in(tmp_path, mode=0o644)
    c = by_name(run(settings(tmp_path)))["Sign-in"]
    assert c.status == FAIL and "chmod 600" in c.detail


def test_not_signed_in_is_a_warning(tmp_path):
    checks = run(settings(tmp_path))
    c = by_name(checks)["Sign-in"]
    assert c.status == WARN and "fleetwatch login" in c.detail
    assert exit_code(checks) == 0


def test_static_token_counts_as_signed_in(tmp_path):
    assert by_name(run(settings(tmp_path, epiphan_token="t")))["Sign-in"].status == OK


def test_write_tool_on_the_read_list_fails(tmp_path):
    signed_in(tmp_path)
    bad = tmp_path / "tool_policy.yaml"
    bad.write_text("read:\n  - get_devices_in_my_team\n  - batch_reboot\nwrite: []\n")
    checks = run(settings(tmp_path, tool_policy_file=bad))
    c = by_name(checks)["Read-only guard"]
    assert c.status == FAIL and "batch_reboot" in c.detail
    assert exit_code(checks) == 1


def test_policy_that_asks_for_autonomy_fails(tmp_path):
    signed_in(tmp_path)
    p = tmp_path / "policy.yaml"
    p.write_text("autonomy: auto\n")
    assert by_name(run(settings(tmp_path, policy_file=p)))["Policy"].status == FAIL


def test_offline_is_a_warning_not_a_failure(tmp_path):
    signed_in(tmp_path)
    checks = run(settings(tmp_path), reach=False)
    assert by_name(checks)["Epiphan reachable"].status == WARN
    assert exit_code(checks) == 0


def test_slack_is_checked_only_with_a_token(tmp_path):
    signed_in(tmp_path)
    assert "Slack reachable" not in by_name(run(settings(tmp_path)))
    assert by_name(run(settings(tmp_path, slack_bot_token="xoxb-test")))["Slack reachable"].status == OK


def test_service_not_installed_is_a_warning(tmp_path):
    signed_in(tmp_path)
    c = by_name(run(settings(tmp_path), service=(WARN, "not installed: run deploy/install.sh")))["Service"]
    assert c.status == WARN


def test_report_summary(tmp_path, capsys):
    from fleetwatch.doctor import print_report

    signed_in(tmp_path)
    print_report(run(settings(tmp_path)))
    assert capsys.readouterr().out.rstrip().endswith("All good.")
    print_report(run(settings(tmp_path), reach=False))
    assert "Nothing broken. 1 to look at." in capsys.readouterr().out
