"""CLI surface that doesn't need Epiphan."""

import sys

import pytest

from fleetwatch import cli


def test_version_flag_prints_the_package_version(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["fleetwatch", "--version"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("fleetwatch ") and out.split()[1][0].isdigit()


def test_replay_digest_ignores_quiet_hours(monkeypatch, capsys):
    # A replay is a demo against an in-memory state; it must show the full digest at any hour (CI runs at night).
    from fleetwatch.policy import Policy

    monkeypatch.setattr(Policy, "in_quiet_hours", lambda self, now: self.quiet_start is not None)
    monkeypatch.setattr(sys, "argv", ["fleetwatch", "digest", "--replay", "tests/fixtures"])
    cli.main()
    out = capsys.readouterr().out
    assert "Fleet check" in out
    assert "Fix soon" in out
