"""Runtime settings from the environment (or a .env file). Behaviour lives in policy.yaml."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

HOME = Path.home() / ".fleetwatch"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLEETWATCH_", env_file=".env", extra="ignore")

    epiphan_mcp_url: str = "https://go.epiphan.cloud/mcp"
    # A static bearer token, for hosts that can't run the browser sign-in. Normally unset.
    epiphan_token: str | None = None
    oauth_callback_port: int = 8765
    ask_port: int = 8766  # fleetwatch ask --serve, on 127.0.0.1 only

    slack_bot_token: str | None = None
    slack_channel: str = "#av-ops"
    # Power Automate Workflows webhook for a Teams channel. A secret: the URL alone lets anyone post.
    teams_webhook_url: SecretStr | None = None
    # Socket Mode app-level token (xapp-, connections:write). Set it to answer /fleetwatch in Slack.
    slack_app_token: str | None = None

    policy_file: Path = Path("policy.yaml")
    tool_policy_file: Path = Path("tool_policy.yaml")
    state_db: Path = HOME / "state.db"
    token_file: Path = HOME / "epiphan-oauth.json"
    # auto | file | keychain | systemd-creds. auto: Keychain on macOS, systemd-creds on systemd 256+, else the file.
    token_store: str = "auto"
