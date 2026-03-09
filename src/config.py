from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "frozen": True}

    # Required
    telegram_bot_token: str
    allowed_user_ids: str  # comma-separated

    # Claude CLI
    claude_cli_path: str = "claude"
    claude_model: str = ""  # empty = use CLI default
    claude_permission_mode: str = "bypassPermissions"
    claude_max_budget_usd: float = 0  # 0 = no limit

    # Paths
    workspace_dir: str = "."
    db_path: str = "data/sessions.db"

    # Limits
    max_sessions_per_user: int = 10
    cli_timeout: int = 0  # 0 = no limit

    # Logging
    log_level: str = "INFO"

    @field_validator("allowed_user_ids")
    @classmethod
    def validate_user_ids(cls, v: str) -> str:
        for uid in v.split(","):
            uid = uid.strip()
            if uid and not uid.isdigit():
                raise ValueError(f"Invalid user ID: {uid}")
        return v

    def get_allowed_user_ids(self) -> set[int]:
        return {int(uid.strip()) for uid in self.allowed_user_ids.split(",") if uid.strip()}

    def get_workspace_path(self) -> Path:
        return Path(self.workspace_dir).resolve()

    def get_db_path(self) -> Path:
        return Path(self.db_path).resolve()


def load_settings() -> Settings:
    return Settings()
