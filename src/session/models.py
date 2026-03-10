from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Session:
    id: str
    user_id: int
    name: str
    claude_session_id: str | None  # Claude CLI session ID
    is_active: bool
    topic_id: int | None  # Telegram forum topic ID
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    created_at: datetime = None  # type: ignore[assignment]
    updated_at: datetime = None  # type: ignore[assignment]
