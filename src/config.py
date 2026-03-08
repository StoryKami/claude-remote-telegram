from __future__ import annotations

import platform
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "frozen": True}

    # Required
    telegram_bot_token: str
    anthropic_api_key: str
    allowed_user_ids: str  # comma-separated

    # Claude
    claude_model: str = "claude-sonnet-4-20250514"
    claude_max_tokens: int = 8192

    # Paths
    workspace_dir: str = "./workspace"
    db_path: str = "data/sessions.db"

    # Limits
    max_sessions_per_user: int = 10
    session_max_messages: int = 200
    bash_timeout: int = 30
    bash_max_timeout: int = 300
    max_output_size: int = 51200  # 50KB
    max_file_size: int = 1048576  # 1MB
    max_tool_loops: int = 20

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

    def get_system_prompt(self) -> str:
        ws = self.get_workspace_path()
        return (
            "You are a remote coding assistant accessible via Telegram.\n"
            "You can execute bash commands, read/write files on the server.\n"
            "\n"
            "Environment:\n"
            f"- OS: {platform.system()} {platform.release()}\n"
            f"- Platform: {platform.platform()}\n"
            f"- Workspace: {ws}\n"
            "\n"
            "Rules:\n"
            "- Never execute destructive commands (rm -rf /, shutdown, reboot, etc.)\n"
            "- Read file contents before modifying them.\n"
            "- Keep responses concise and Telegram-friendly.\n"
            "- Use markdown code blocks for code.\n"
            "- When executing commands, prefer the workspace directory.\n"
        )


def load_settings() -> Settings:
    return Settings()
