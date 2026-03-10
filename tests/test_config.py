"""Tests for config validation."""
import os
import pytest
from src.config import Settings


def test_valid_user_ids():
    s = Settings(
        telegram_bot_token="test:token",
        allowed_user_ids="123,456",
    )
    assert s.get_allowed_user_ids() == {123, 456}


def test_single_user_id():
    s = Settings(
        telegram_bot_token="test:token",
        allowed_user_ids="123",
    )
    assert s.get_allowed_user_ids() == {123}


def test_invalid_user_id():
    with pytest.raises(ValueError, match="Invalid user ID"):
        Settings(
            telegram_bot_token="test:token",
            allowed_user_ids="abc",
        )


def test_defaults(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    s = Settings(
        telegram_bot_token="test:token",
        allowed_user_ids="1",
        _env_file=None,  # don't read .env
    )
    assert s.claude_permission_mode == "bypassPermissions"
    assert s.max_sessions_per_user == 50
    assert s.cli_timeout == 0
    assert s.log_level == "INFO"
