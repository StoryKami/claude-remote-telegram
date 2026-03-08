from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from src.session.models import Message, Session

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    model TEXT NOT NULL,
    system_prompt TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
"""


class SessionRepository:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.commit()
        logger.info("Database initialized: %s", self._db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # -- Sessions --

    async def create_session(self, session: Session) -> None:
        assert self._db
        await self._db.execute(
            "INSERT INTO sessions (id, user_id, name, model, system_prompt, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session.id,
                session.user_id,
                session.name,
                session.model,
                session.system_prompt,
                int(session.is_active),
                session.created_at.isoformat(),
                session.updated_at.isoformat(),
            ),
        )
        await self._db.commit()

    async def get_session(self, session_id: str) -> Session | None:
        assert self._db
        cursor = await self._db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = await cursor.fetchone()
        return _row_to_session(row) if row else None

    async def get_active_session(self, user_id: int) -> Session | None:
        assert self._db
        cursor = await self._db.execute(
            "SELECT * FROM sessions WHERE user_id = ? AND is_active = 1 ORDER BY updated_at DESC LIMIT 1",
            (user_id,),
        )
        row = await cursor.fetchone()
        return _row_to_session(row) if row else None

    async def get_user_sessions(self, user_id: int) -> list[Session]:
        assert self._db
        cursor = await self._db.execute(
            "SELECT * FROM sessions WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [_row_to_session(row) for row in rows]

    async def update_session(self, session_id: str, **fields: object) -> None:
        assert self._db
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values())
        values.append(session_id)
        await self._db.execute(
            f"UPDATE sessions SET {set_clause} WHERE id = ?",  # noqa: S608
            values,
        )
        await self._db.commit()

    async def deactivate_user_sessions(self, user_id: int) -> None:
        assert self._db
        await self._db.execute(
            "UPDATE sessions SET is_active = 0 WHERE user_id = ?", (user_id,)
        )
        await self._db.commit()

    async def delete_session(self, session_id: str) -> None:
        assert self._db
        await self._db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        await self._db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await self._db.commit()

    async def count_user_sessions(self, user_id: int) -> int:
        assert self._db
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    # -- Messages --

    async def add_message(self, message: Message) -> None:
        assert self._db
        await self._db.execute(
            "INSERT INTO messages (id, session_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                message.id,
                message.session_id,
                message.role,
                message.content,
                message.created_at.isoformat(),
            ),
        )
        await self._db.commit()

    async def get_messages(self, session_id: str, limit: int = 200) -> list[Message]:
        assert self._db
        cursor = await self._db.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC LIMIT ?",
            (session_id, limit),
        )
        rows = await cursor.fetchall()
        return [_row_to_message(row) for row in rows]

    async def clear_messages(self, session_id: str) -> None:
        assert self._db
        await self._db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        await self._db.commit()

    async def count_messages(self, session_id: str) -> int:
        assert self._db
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


def _row_to_session(row: aiosqlite.Row) -> Session:
    return Session(
        id=row["id"],
        user_id=row["user_id"],
        name=row["name"],
        model=row["model"],
        system_prompt=row["system_prompt"],
        is_active=bool(row["is_active"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_message(row: aiosqlite.Row) -> Message:
    return Message(
        id=row["id"],
        session_id=row["session_id"],
        role=row["role"],
        content=row["content"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
