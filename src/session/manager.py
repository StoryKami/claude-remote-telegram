from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.session.models import Session

if TYPE_CHECKING:
    from src.session.repository import SessionRepository

logger = logging.getLogger(__name__)


class SessionManager:
    def __init__(self, repository: SessionRepository, max_sessions: int = 10) -> None:
        self._repo = repository
        self._max_sessions = max_sessions

    async def get_or_create_active(self, user_id: int) -> Session:
        session = await self._repo.get_active_session(user_id)
        if session:
            return session
        return await self.create_session(user_id, "default")

    async def create_session(self, user_id: int, name: str) -> Session:
        count = await self._repo.count_user_sessions(user_id)
        if count >= self._max_sessions:
            raise ValueError(
                f"Maximum sessions ({self._max_sessions}) reached. Delete old sessions first."
            )

        await self._repo.deactivate_user_sessions(user_id)

        now = datetime.now(timezone.utc)
        session = Session(
            id=uuid.uuid4().hex[:12],
            user_id=user_id,
            name=name,
            claude_session_id=None,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        await self._repo.create_session(session)
        logger.info("Created session: %s for user %d", session.id, user_id)
        return session

    async def switch_session(self, user_id: int, session_id: str) -> Session:
        session = await self._repo.get_session(session_id)
        if not session or session.user_id != user_id:
            raise ValueError(f"Session not found: {session_id}")

        await self._repo.deactivate_user_sessions(user_id)
        await self._repo.update_session(session_id, is_active=1)

        updated = await self._repo.get_session(session_id)
        assert updated
        logger.info("Switched to session: %s", session_id)
        return updated

    async def get_user_sessions(self, user_id: int) -> list[Session]:
        return await self._repo.get_user_sessions(user_id)

    async def delete_session(self, user_id: int, session_id: str) -> None:
        session = await self._repo.get_session(session_id)
        if not session or session.user_id != user_id:
            raise ValueError(f"Session not found: {session_id}")
        await self._repo.delete_session(session_id)
        logger.info("Deleted session: %s", session_id)

    async def rename_session(self, user_id: int, session_id: str, name: str) -> None:
        session = await self._repo.get_session(session_id)
        if not session or session.user_id != user_id:
            raise ValueError(f"Session not found: {session_id}")
        await self._repo.update_session(session_id, name=name)

    async def set_claude_session_id(self, session_id: str, claude_session_id: str) -> None:
        await self._repo.update_session(session_id, claude_session_id=claude_session_id)

    async def touch_session(self, session_id: str) -> None:
        await self._repo.update_session(session_id)
