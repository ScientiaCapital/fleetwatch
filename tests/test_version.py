import tomllib
from importlib.metadata import version
from pathlib import Path

import fleetwatch


def test_the_version_is_the_same_everywhere():
    """A release sets pyproject.toml and __version__ in one PR; `fleetwatch --version` reads the package metadata."""
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert fleetwatch.__version__ == pyproject["project"]["version"] == version("fleetwatch")
