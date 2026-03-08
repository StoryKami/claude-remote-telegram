from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.session.models import Message, Session

if TYPE_CHECKING:
    from src.session.repository import SessionRepository

logger = logging.getLogger(__name__)


class SessionManager:
    def __init__(
        self,
        repository: SessionRepository,
        max_sessions: int = 10,
        max_messages: int = 200,
        default_model: str = "claude-sonnet-4-20250514",
    ) -> None:
        self._repo = repository
        self._max_sessions = max_sessions
        self._max_messages = max_messages
        self._default_model = default_model

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
            model=self._default_model,
            system_prompt=None,
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

    async def clear_messages(self, session_id: str) -> None:
        await self._repo.clear_messages(session_id)
        logger.info("Cleared messages for session: %s", session_id)

    async def set_system_prompt(self, session_id: str, prompt: str | None) -> None:
        await self._repo.update_session(session_id, system_prompt=prompt)

    async def set_model(self, session_id: str, model: str) -> None:
        await self._repo.update_session(session_id, model=model)

    async def add_message(
        self, session_id: str, role: str, content: str | list[dict]
    ) -> None:
        content_str = json.dumps(content, ensure_ascii=False) if isinstance(content, list) else content
        message = Message(
            id=uuid.uuid4().hex[:16],
            session_id=session_id,
            role=role,
            content=content_str,
            created_at=datetime.now(timezone.utc),
        )
        await self._repo.add_message(message)

    async def get_history(self, session_id: str) -> list[dict]:
        messages = await self._repo.get_messages(session_id, self._max_messages)
        result = []
        for msg in messages:
            try:
                content = json.loads(msg.content)
            except (json.JSONDecodeError, TypeError):
                content = msg.content
            result.append({"role": msg.role, "content": content})
        return result

    async def get_message_count(self, session_id: str) -> int:
        return await self._repo.count_messages(session_id)
