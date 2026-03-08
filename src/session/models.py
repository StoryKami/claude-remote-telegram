from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Session:
    id: str
    user_id: int
    name: str
    model: str
    system_prompt: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Message:
    id: str
    session_id: str
    role: str  # "user" | "assistant"
    content: str  # JSON-serialized content blocks
    created_at: datetime


@dataclass(frozen=True)
class ToolResult:
    success: bool
    output: str
    error: str | None = None
