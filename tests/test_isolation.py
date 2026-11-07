"""No test reads or writes a real sign-in (#163): not the macOS Keychain, not systemd-creds, not ~/.fleetwatch."""

import sys

import pytest

from fleetwatch.config import HOME, Settings
from fleetwatch.epiphan.token_store import KeychainTokenStorage, make_token_store


def test_settings_point_at_a_temporary_file_store():
    s = Settings(_env_file=None)
    assert s.token_store == "file"
    for path in (s.token_file, s.sandbox_token_file):
        assert not path.is_relative_to(HOME), f"{path} is the real sign-in"


def test_the_store_a_command_builds_is_the_temporary_file():
    s = Settings(_env_file=None)
    store = make_token_store(s.token_store, s.token_file)
    assert not store.is_dead() and not store.has_tokens()


@pytest.mark.skipif(sys.platform != "darwin", reason="the Keychain is macOS only")
def test_an_unfaked_keychain_call_fails_the_test():
    with pytest.raises(AssertionError, match="real"):
        KeychainTokenStorage().has_tokens()
