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
    created_at: datetime
    updated_at: datetime
