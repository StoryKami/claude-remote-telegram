"""Tests for handler utilities."""
import re
from src.bot.handlers import _is_valid_session_id, _extract_text


class TestIsValidSessionId:
    def test_valid_hex_12(self):
        assert _is_valid_session_id("abc123def456")

    def test_valid_uuid(self):
        assert _is_valid_session_id("a5a4eb19-f8c4-46ee-9513-cd139a7d50bb")

    def test_valid_short_hex(self):
        assert _is_valid_session_id("abcd1234")

    def test_invalid_path_traversal(self):
        assert not _is_valid_session_id("../../etc/passwd")

    def test_invalid_slash(self):
        assert not _is_valid_session_id("abc/def")

    def test_invalid_spaces(self):
        assert not _is_valid_session_id("abc def")

    def test_invalid_too_short(self):
        assert not _is_valid_session_id("abc")

    def test_invalid_too_long(self):
        assert not _is_valid_session_id("a" * 37)

    def test_invalid_non_hex(self):
        assert not _is_valid_session_id("ghijklmn")


class TestExtractText:
    def test_string_content(self):
        assert _extract_text("hello world") == "hello world"

    def test_string_strips(self):
        assert _extract_text("  hello  ") == "hello"

    def test_list_text_blocks(self):
        content = [
            {"type": "text", "text": "hello"},
            {"type": "text", "text": "world"},
        ]
        assert _extract_text(content) == "hello\nworld"

    def test_list_mixed_blocks(self):
        content = [
            {"type": "thinking", "thinking": "hmm"},
            {"type": "text", "text": "answer"},
        ]
        assert _extract_text(content) == "answer"

    def test_empty_list(self):
        assert _extract_text([]) == ""

    def test_none(self):
        assert _extract_text(None) == ""

    def test_number(self):
        assert _extract_text(42) == ""
