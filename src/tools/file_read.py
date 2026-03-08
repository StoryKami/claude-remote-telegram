from __future__ import annotations

import logging

from src.security.sandbox import Sandbox
from src.session.models import ToolResult

logger = logging.getLogger(__name__)


async def execute_read_file(params: dict, sandbox: Sandbox) -> ToolResult:
    path_str = params.get("path", "")
    offset = params.get("offset", 0)
    limit = params.get("limit", 2000)

    if not path_str:
        return ToolResult(success=False, output="", error="No path provided")

    try:
        resolved = sandbox.validate_path(path_str)
    except PermissionError as e:
        return ToolResult(success=False, output="", error=str(e))

    if not resolved.exists():
        return ToolResult(success=False, output="", error=f"File not found: {resolved}")

    if not resolved.is_file():
        return ToolResult(success=False, output="", error=f"Not a file: {resolved}")

    try:
        sandbox.validate_file_size(resolved.stat().st_size)
    except ValueError as e:
        return ToolResult(success=False, output="", error=str(e))

    try:
        raw = resolved.read_bytes()
        if b"\x00" in raw[:8192]:
            return ToolResult(
                success=True,
                output=f"[Binary file: {len(raw)} bytes]",
            )
        text = raw.decode("utf-8", errors="replace")
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Read error: {e}")

    lines = text.splitlines()
    selected = lines[offset : offset + limit]
    numbered = [f"{i + offset + 1:>5}\t{line}" for i, line in enumerate(selected)]
    output = "\n".join(numbered)

    if len(lines) > offset + limit:
        output += f"\n\n... ({len(lines)} lines total, showing {offset + 1}-{offset + len(selected)})"

    logger.info("Read file: %s (%d lines)", resolved, len(selected))
    return ToolResult(success=True, output=output)
