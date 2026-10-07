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
