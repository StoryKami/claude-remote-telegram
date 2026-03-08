from __future__ import annotations

import re

# Characters that must be escaped in Telegram MarkdownV2
_ESCAPE_CHARS = r"_*[]()~`>#+-=|{}.!\\"
_ESCAPE_RE = re.compile(f"([{re.escape(_ESCAPE_CHARS)}])")

# Match markdown code blocks
_CODE_BLOCK_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")

TELEGRAM_MSG_LIMIT = 4096


def format_telegram_message(text: str) -> list[str]:
    """Format Claude's markdown response for Telegram and split if needed."""
    if not text.strip():
        return ["(empty response)"]

    chunks = split_message(text, TELEGRAM_MSG_LIMIT)
    return chunks


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

        # Try to split at a good boundary
        cut = _find_split_point(remaining, limit)
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")

    return chunks


def _find_split_point(text: str, limit: int) -> int:
    """Find a good point to split text, preferring paragraph and line boundaries."""
    segment = text[:limit]

    # Check if we're inside a code block
    open_blocks = segment.count("```")
    in_code_block = open_blocks % 2 == 1

    if in_code_block:
        # Find the start of this code block and split before it
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

    # Hard cut
    return limit


def escape_markdown_v2(text: str) -> str:
    """Escape text for Telegram MarkdownV2, preserving code blocks."""
    parts: list[str] = []
    last_end = 0

    for match in _CODE_BLOCK_RE.finditer(text):
        # Escape text before code block
        before = text[last_end : match.start()]
        parts.append(_escape_non_code(before))
        # Keep code block as-is (Telegram handles it)
        lang = match.group(1)
        code = match.group(2)
        parts.append(f"```{lang}\n{code}```")
        last_end = match.end()

    # Remaining text after last code block
    after = text[last_end:]
    parts.append(_escape_non_code(after))

    return "".join(parts)


def _escape_non_code(text: str) -> str:
    """Escape non-code text, preserving inline code."""
    parts: list[str] = []
    last_end = 0

    for match in _INLINE_CODE_RE.finditer(text):
        before = text[last_end : match.start()]
        parts.append(_ESCAPE_RE.sub(r"\\\1", before))
        parts.append(f"`{match.group(1)}`")
        last_end = match.end()

    after = text[last_end:]
    parts.append(_ESCAPE_RE.sub(r"\\\1", after))

    return "".join(parts)


def format_tool_status(tool_name: str, params: dict) -> str:
    """Format a tool execution status message."""
    match tool_name:
        case "bash":
            cmd = params.get("command", "")
            if len(cmd) > 60:
                cmd = cmd[:57] + "..."
            return f"[bash] `{cmd}`"
        case "read_file":
            return f"[read] {params.get('path', '?')}"
        case "write_file":
            return f"[write] {params.get('path', '?')}"
        case "list_directory":
            return f"[ls] {params.get('path', '.')}"
        case _:
            return f"[{tool_name}]"


def format_session_info(
    session_id: str,
    name: str,
    model: str,
    msg_count: int,
    is_active: bool,
) -> str:
    """Format session info for display."""
    active_mark = " (active)" if is_active else ""
    return (
        f"Session: {name}{active_mark}\n"
        f"ID: {session_id}\n"
        f"Model: {model}\n"
        f"Messages: {msg_count}"
    )
