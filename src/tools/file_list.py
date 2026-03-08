from __future__ import annotations

import logging
from datetime import datetime

from src.security.sandbox import Sandbox
from src.session.models import ToolResult

logger = logging.getLogger(__name__)

MAX_ENTRIES = 500


async def execute_list_directory(params: dict, sandbox: Sandbox) -> ToolResult:
    path_str = params.get("path", "")

    try:
        resolved = sandbox.validate_path(path_str) if path_str else sandbox.workspace
    except PermissionError as e:
        return ToolResult(success=False, output="", error=str(e))

    if not resolved.exists():
        return ToolResult(success=False, output="", error=f"Directory not found: {resolved}")

    if not resolved.is_dir():
        return ToolResult(success=False, output="", error=f"Not a directory: {resolved}")

    try:
        entries = sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return ToolResult(success=False, output="", error=f"Permission denied: {resolved}")

    lines: list[str] = []
    for i, entry in enumerate(entries):
        if i >= MAX_ENTRIES:
            lines.append(f"\n... ({len(list(resolved.iterdir()))} entries total, showing {MAX_ENTRIES})")
            break
        try:
            stat = entry.stat()
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            if entry.is_dir():
                lines.append(f"  {modified}  {'<DIR>':>10}  {entry.name}/")
            else:
                size = _format_size(stat.st_size)
                lines.append(f"  {modified}  {size:>10}  {entry.name}")
        except OSError:
            lines.append(f"  {'?':>27}  {entry.name}")

    output = f"Directory: {resolved}\n\n" + "\n".join(lines) if lines else f"Directory: {resolved}\n\n(empty)"

    logger.info("Listed directory: %s (%d entries)", resolved, len(lines))
    return ToolResult(success=True, output=output)


def _format_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"
