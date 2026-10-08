"""Runtime settings from the environment (or a .env file). Behaviour lives in policy.yaml.

Tokens are `SecretStr`, so a repr, a traceback or a debug log of the settings shows `**********`. Unwrap one with
`reveal()` only where it is handed to the library that uses it.
"""

from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

HOME = Path.home() / ".fleetwatch"


def reveal(secret: SecretStr | None) -> str:
    """The secret's value, or "" when it is unset."""
    return secret.get_secret_value() if secret is not None else ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLEETWATCH_", env_file=".env", extra="ignore")

    epiphan_mcp_url: str = "https://go.epiphan.cloud/mcp"
    # A static bearer token, for hosts that can't run the browser sign-in. Normally unset.
    epiphan_token: SecretStr | None = None
    oauth_callback_port: int = 8765
    ask_port: int = 8766  # fleetwatch ask --serve, on 127.0.0.1 only

    slack_bot_token: SecretStr | None = None
    slack_channel: str = "#av-ops"
    # Power Automate Workflows webhook for a Teams channel. A secret: the URL alone lets anyone post.
    teams_webhook_url: SecretStr | None = None
    # Socket Mode app-level token (xapp-, connections:write). Set it to answer /fleetwatch in Slack.
    slack_app_token: SecretStr | None = None

    policy_file: Path = Path("policy.yaml")
    # Unset: the read list that ships inside the package. Set: a shorter read list; it can never add a tool.
    tool_policy_file: Path | None = None
    state_db: Path = HOME / "state.db"
    token_file: Path = HOME / "epiphan-oauth.json"
    # auto | file | keychain | systemd-creds. auto: Keychain on macOS, systemd-creds on systemd 256+, else the file.
    token_store: str = "auto"

    # For v0.2 (docs/design/approved-writes.md). `fleetwatch ask` reads the key and model.
    # The assistant's model key. Empty: no question or fleet data goes to Anthropic; the keyword `ask` answers.
    anthropic_api_key: SecretStr | None = None
    ai_model: str = "claude-haiku-5-5"
    # Read by the write executor (src/fleetwatch/epiphan/executor.py). Set: a change runs only when the sandbox
    # sign-in reports this team ID; if Epiphan reports none, the change is refused. Empty: no team check.
    write_team_id: str = ""
    # The sandbox sign-in's own slot, separate from token_file (`fleetwatch login --sandbox`). Only the write
    # executor loads it; the heartbeat never does.
    sandbox_token_file: Path = HOME / "epiphan-sandbox-oauth.json"

    @field_validator("epiphan_mcp_url")
    @classmethod
    def _https_only(cls, v: str) -> str:
        if not v.strip().lower().startswith("https://"):
            raise ValueError(
                "FLEETWATCH_EPIPHAN_MCP_URL must start with https:// (for example https://go.epiphan.cloud/mcp); "
                "the sign-in token travels on every request"
            )
        return v.strip()
