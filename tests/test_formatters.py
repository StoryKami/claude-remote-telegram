"""Tests for message formatting and splitting."""
from src.bot.formatters import format_telegram_message, split_message, TELEGRAM_MSG_LIMIT


def test_short_message():
    chunks = format_telegram_message("hello")
    assert chunks == ["hello"]


def test_empty_message():
    chunks = format_telegram_message("")
    assert chunks == ["(empty response)"]


def test_whitespace_message():
    chunks = format_telegram_message("   \n  ")
    assert chunks == ["(empty response)"]


def test_long_message_splits():
    text = "a" * (TELEGRAM_MSG_LIMIT + 100)
    chunks = split_message(text)
    assert len(chunks) >= 2
    assert all(len(c) <= TELEGRAM_MSG_LIMIT for c in chunks)
    assert "".join(c.strip() for c in chunks) == text


def test_split_at_paragraph():
    text = "A" * 2500 + "\n\n" + "B" * 2000
    chunks = split_message(text, limit=3000)
    assert len(chunks) == 2
    assert chunks[0].strip().endswith("A" * 10)
    assert chunks[1].strip().startswith("B" * 10)


def test_split_at_newline():
    text = "A" * 2500 + "\n" + "B" * 2000
    chunks = split_message(text, limit=3000)
    assert len(chunks) == 2


def test_split_preserves_small_code_blocks():
    """Code blocks smaller than limit should not be split."""
    text = "intro\n\n```python\n" + "x = 1\n" * 50 + "```\n\nafter some more text here"
    chunks = split_message(text, limit=2000)
    # The code block should be entirely in one chunk
    for chunk in chunks:
        opens = chunk.count("```")
        assert opens % 2 == 0, f"Code block split incorrectly in chunk: {chunk[:50]}..."
