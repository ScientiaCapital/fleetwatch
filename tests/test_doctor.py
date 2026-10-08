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
        "token_store": "file",  # never the real keychain from a test
        "slack_bot_token": None,
        "teams_webhook_url": None,
        "slack_app_token": None,
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


def test_other_stores_report_where_the_token_is(tmp_path, monkeypatch):
    from fleetwatch import doctor

    class Store:
        where = "macOS Keychain (fleetwatch-epiphan)"

        def has_tokens(self):
            return True

    monkeypatch.setattr(doctor, "make_token_store", lambda kind, path: Store())
    c = by_name(run(settings(tmp_path, token_store="keychain")))["Sign-in"]
    assert c.status == OK and "Keychain" in c.detail


def test_token_store_trouble_fails(tmp_path, monkeypatch):
    from fleetwatch import doctor
    from fleetwatch.epiphan.token_store import TokenStoreError

    def broken(kind, path):
        raise TokenStoreError("Keychain: the keychain is locked")

    monkeypatch.setattr(doctor, "make_token_store", broken)
    c = by_name(run(settings(tmp_path, token_store="keychain")))["Sign-in"]
    assert c.status == FAIL and "locked" in c.detail


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


def test_teams_row_says_configured_without_showing_the_url_or_calling_it(tmp_path):
    reached = []
    url = "https://prod-00.example.com/workflows/x/triggers/manual/paths/invoke?sig=FAKESIG9"
    checks = run_checks(
        settings(tmp_path, teams_webhook_url=url), reach=lambda u: reached.append(u) or True, service=lambda: (OK, "")
    )
    teams = by_name(checks)["Teams"]
    assert teams.status == OK and teams.detail == "configured"
    assert url not in reached
    assert by_name(run(settings(tmp_path)))["Teams"].detail == "not configured"


def test_teams_webhook_must_be_https(tmp_path):
    teams = by_name(run(settings(tmp_path, teams_webhook_url="http://example.com/x?sig=FAKESIG9")))["Teams"]
    assert teams.status == WARN and "FAKESIG9" not in teams.detail


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


def test_slack_commands_off_without_an_app_token(tmp_path):
    c = by_name(run(settings(tmp_path)))["Slack commands"]
    assert c.status == OK and "off" in c.detail


def test_slack_commands_need_an_xapp_token(tmp_path):
    checks = run(settings(tmp_path, slack_app_token="xoxb-wrong-kind-of-token"))
    c = by_name(checks)["Slack commands"]
    assert c.status == FAIL and "xapp-" in c.detail and "wrong-kind" not in c.detail
    assert exit_code(checks) == 1


def test_slack_commands_with_nobody_allowed_is_a_warning(tmp_path):
    c = by_name(run(settings(tmp_path, slack_app_token="xapp-1-test")))["Slack commands"]
    assert c.status == WARN and "allowed_user_ids" in c.detail


def test_slack_commands_ready(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("slack:\n  allowed_user_ids: [U0TEST1]\n")
    c = by_name(run(settings(tmp_path, policy_file=p, slack_app_token="xapp-1-test")))["Slack commands"]
    assert c.status == OK and "1 user" in c.detail and "xapp-1-test" not in c.detail


def test_slack_usergroup_needs_the_bot_token(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("slack:\n  allowed_usergroup: S0GROUP\n")
    c = by_name(run(settings(tmp_path, policy_file=p, slack_app_token="xapp-1-test")))["Slack commands"]
    assert c.status == WARN and "FLEETWATCH_SLACK_BOT_TOKEN" in c.detail
