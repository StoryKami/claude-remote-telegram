"""Tests for auth service."""
from src.security.auth import AuthService


def test_authorized_user():
    auth = AuthService({123, 456})
    assert auth.is_authorized(123)
    assert auth.is_authorized(456)


def test_unauthorized_user():
    auth = AuthService({123})
    assert not auth.is_authorized(999)


def test_empty_allowed():
    auth = AuthService(set())
    assert not auth.is_authorized(123)
