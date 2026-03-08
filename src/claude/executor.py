from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.session.models import ToolResult
from src.tools.bash import execute_bash
from src.tools.file_list import execute_list_directory
from src.tools.file_read import execute_read_file
from src.tools.file_write import execute_write_file

if TYPE_CHECKING:
    from src.config import Settings
    from src.security.sandbox import Sandbox

logger = logging.getLogger(__name__)


class ToolExecutor:
    def __init__(self, sandbox: Sandbox, settings: Settings) -> None:
        self._sandbox = sandbox
        self._settings = settings

    async def execute(self, tool_name: str, params: dict) -> ToolResult:
        logger.info("Tool call: %s(%s)", tool_name, _truncate_params(params))

        match tool_name:
            case "bash":
                return await execute_bash(
                    params,
                    self._sandbox,
                    default_timeout=self._settings.bash_timeout,
                    max_timeout=self._settings.bash_max_timeout,
                    max_output_size=self._settings.max_output_size,
                )
            case "read_file":
                return await execute_read_file(params, self._sandbox)
            case "write_file":
                return await execute_write_file(params, self._sandbox)
            case "list_directory":
                return await execute_list_directory(params, self._sandbox)
            case _:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Unknown tool: {tool_name}",
                )


def _truncate_params(params: dict) -> str:
    items = []
    for k, v in params.items():
        s = str(v)
        if len(s) > 100:
            s = s[:100] + "..."
        items.append(f"{k}={s}")
    return ", ".join(items)
