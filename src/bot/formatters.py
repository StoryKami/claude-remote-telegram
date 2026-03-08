from __future__ import annotations

TELEGRAM_MSG_LIMIT = 4096


def format_telegram_message(text: str) -> list[str]:
    """Format Claude's markdown response for Telegram and split if needed."""
    if not text.strip():
        return ["(empty response)"]
    return split_message(text, TELEGRAM_MSG_LIMIT)


def split_message(text: str, limit: int = TELEGRAM_MSG_LIMIT) -> list[str]:
    """Split a long message into chunks, respecting code block boundaries."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        cut = _find_split_point(remaining, limit)
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")

    return chunks


def _find_split_point(text: str, limit: int) -> int:
    """Find a good point to split text, preferring paragraph and line boundaries."""
    segment = text[:limit]

    # Check if we're inside a code block
    open_blocks = segment.count("```")
    if open_blocks % 2 == 1:
        last_open = segment.rfind("```")
        if last_open > limit // 4:
            return last_open

    # Try double newline (paragraph break)
    pos = segment.rfind("\n\n")
    if pos > limit // 2:
        return pos + 2

    # Try single newline
    pos = segment.rfind("\n")
    if pos > limit // 2:
        return pos + 1

    # Try space
    pos = segment.rfind(" ")
    if pos > limit // 2:
        return pos + 1

    return limit
