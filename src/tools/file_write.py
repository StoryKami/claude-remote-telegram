from __future__ import annotations

import logging

from src.security.sandbox import Sandbox
from src.session.models import ToolResult

logger = logging.getLogger(__name__)


async def execute_write_file(params: dict, sandbox: Sandbox) -> ToolResult:
    path_str = params.get("path", "")
    content = params.get("content", "")

    if not path_str:
        return ToolResult(success=False, output="", error="No path provided")

    try:
        resolved = sandbox.validate_path(path_str)
    except PermissionError as e:
        return ToolResult(success=False, output="", error=str(e))

    try:
        sandbox.validate_file_size(len(content.encode("utf-8")))
    except ValueError as e:
        return ToolResult(success=False, output="", error=str(e))

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Write error: {e}")

    logger.info("Wrote file: %s (%d bytes)", resolved, len(content))
    return ToolResult(
        success=True,
        output=f"Written {len(content)} bytes to {resolved}",
    )
