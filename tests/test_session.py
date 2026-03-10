"""Tests for session manager and repository."""
import asyncio
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from src.session.manager import SessionManager
from src.session.repository import SessionRepository


@pytest_asyncio.fixture
async def repo(tmp_path):
    db_path = tmp_path / "test.db"
    r = SessionRepository(db_path)
    await r.initialize()
    yield r
    await r.close()


@pytest_asyncio.fixture
async def manager(repo):
    return SessionManager(repository=repo, max_sessions=5)


@pytest.mark.asyncio
async def test_create_session(manager):
    session = await manager.create_session(user_id=1, name="test")
    assert session.name == "test"
    assert session.user_id == 1
    assert session.is_active
    assert session.claude_session_id is None
    assert session.topic_id is None


@pytest.mark.asyncio
async def test_create_session_with_topic(manager):
    session = await manager.create_session(user_id=1, name="topic-test", topic_id=42)
    assert session.topic_id == 42


@pytest.mark.asyncio
async def test_get_or_create_active(manager):
    # First call creates default
    s1 = await manager.get_or_create_active(user_id=1)
    assert s1.name == "default"

    # Second call returns same
    s2 = await manager.get_or_create_active(user_id=1)
    assert s2.id == s1.id


@pytest.mark.asyncio
async def test_switch_session(manager):
    s1 = await manager.create_session(user_id=1, name="first")
    s2 = await manager.create_session(user_id=1, name="second")

    # s2 is active (created last)
    active = await manager.get_or_create_active(user_id=1)
    assert active.id == s2.id

    # Switch to s1
    switched = await manager.switch_session(user_id=1, session_id=s1.id)
    assert switched.id == s1.id
    assert switched.is_active


@pytest.mark.asyncio
async def test_switch_wrong_user(manager):
    s = await manager.create_session(user_id=1, name="test")
    with pytest.raises(ValueError, match="Session not found"):
        await manager.switch_session(user_id=999, session_id=s.id)


@pytest.mark.asyncio
async def test_delete_session(manager):
    s = await manager.create_session(user_id=1, name="to-delete")
    await manager.delete_session(user_id=1, session_id=s.id)
    sessions = await manager.get_user_sessions(user_id=1)
    assert len(sessions) == 0


@pytest.mark.asyncio
async def test_delete_wrong_user(manager):
    s = await manager.create_session(user_id=1, name="test")
    with pytest.raises(ValueError, match="Session not found"):
        await manager.delete_session(user_id=999, session_id=s.id)


@pytest.mark.asyncio
async def test_rename_session(manager):
    s = await manager.create_session(user_id=1, name="old")
    await manager.rename_session(user_id=1, session_id=s.id, name="new")
    sessions = await manager.get_user_sessions(user_id=1)
    assert sessions[0].name == "new"


@pytest.mark.asyncio
async def test_max_sessions(manager):
    for i in range(5):
        await manager.create_session(user_id=1, name=f"s{i}")
    with pytest.raises(ValueError, match="Maximum sessions"):
        await manager.create_session(user_id=1, name="overflow")


@pytest.mark.asyncio
async def test_set_claude_session_id(manager):
    s = await manager.create_session(user_id=1, name="test")
    await manager.set_claude_session_id(s.id, "claude-abc-123")
    updated = await manager._repo.get_session(s.id)
    assert updated.claude_session_id == "claude-abc-123"


@pytest.mark.asyncio
async def test_get_session_by_topic(manager):
    s = await manager.create_session(user_id=1, name="topic", topic_id=99)
    found = await manager.get_session_by_topic(99)
    assert found.id == s.id


@pytest.mark.asyncio
async def test_get_session_by_topic_not_found(manager):
    found = await manager.get_session_by_topic(999)
    assert found is None


@pytest.mark.asyncio
async def test_find_by_claude_session_id(manager):
    s = await manager.create_session(user_id=1, name="test")
    await manager.set_claude_session_id(s.id, "uuid-abc")
    found = await manager.find_by_claude_session_id("uuid-abc")
    assert found.id == s.id


@pytest.mark.asyncio
async def test_find_by_claude_session_id_not_found(manager):
    found = await manager.find_by_claude_session_id("nonexistent")
    assert found is None


@pytest.mark.asyncio
async def test_close_and_reopen(manager):
    s = await manager.create_session(user_id=1, name="test")
    await manager.close_session(s.id)
    closed = await manager._repo.get_session(s.id)
    assert not closed.is_active

    await manager.reopen_session(s.id)
    reopened = await manager._repo.get_session(s.id)
    assert reopened.is_active


@pytest.mark.asyncio
async def test_sql_column_whitelist(repo):
    """Verify that update_session rejects non-whitelisted columns."""
    from src.session.models import Session
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    s = Session(id="test123", user_id=1, name="t", claude_session_id=None,
                is_active=True, topic_id=None, created_at=now, updated_at=now)
    await repo.create_session(s)

    with pytest.raises(ValueError, match="Invalid fields"):
        await repo.update_session("test123", evil_column="DROP TABLE sessions")
