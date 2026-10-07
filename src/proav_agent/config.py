"""Runtime settings from the environment (or a .env file). Behaviour lives in policy.yaml."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

HOME = Path.home() / ".proav-agent"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROAV_", env_file=".env", extra="ignore")

    epiphan_mcp_url: str = "https://go.epiphan.cloud/mcp"
    # A static bearer token, for hosts that can't run the browser sign-in. Normally unset.
    epiphan_token: str | None = None
    oauth_callback_port: int = 8765

    slack_bot_token: str | None = None
    slack_channel: str = "#av-ops"

    llm_polish: bool = False
    llm_model: str = "claude-opus-5-5"

    policy_file: Path = Path("policy.yaml")
    tool_policy_file: Path = Path("tool_policy.yaml")
    state_db: Path = HOME / "state.db"
    token_file: Path = HOME / "epiphan-oauth.json"
