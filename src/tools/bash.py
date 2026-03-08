from __future__ import annotations

import asyncio
import logging
import platform

from src.security.sandbox import Sandbox
from src.session.models import ToolResult

logger = logging.getLogger(__name__)


async def execute_bash(
    params: dict,
    sandbox: Sandbox,
    default_timeout: int = 30,
    max_timeout: int = 300,
    max_output_size: int = 51200,
) -> ToolResult:
    command = params.get("command", "")
    timeout = min(params.get("timeout", default_timeout), max_timeout)

    if not command.strip():
        return ToolResult(success=False, output="", error="Empty command")

    try:
        sandbox.validate_command(command)
    except PermissionError as e:
        logger.warning("Blocked command: %s", command)
        return ToolResult(success=False, output="", error=str(e))

    shell_cmd, shell_flag = _get_shell(command)
    logger.info("Executing: %s (timeout=%ds)", command[:200], timeout)

    try:
        process = await asyncio.create_subprocess_exec(
            shell_cmd,
            shell_flag,
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(sandbox.workspace),
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        process.kill()
        return ToolResult(
            success=False,
            output="",
            error=f"Command timed out after {timeout}s",
        )
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Execution error: {e}")

    output = stdout.decode("utf-8", errors="replace")
    err_output = stderr.decode("utf-8", errors="replace")
    combined = output
    if err_output:
        combined = f"{output}\n[stderr]\n{err_output}" if output else f"[stderr]\n{err_output}"

    if len(combined) > max_output_size:
        combined = combined[:max_output_size] + f"\n\n... (truncated, {len(combined)} bytes total)"

    return ToolResult(
        success=process.returncode == 0,
        output=combined,
        error=None if process.returncode == 0 else f"Exit code: {process.returncode}",
    )


def _get_shell(command: str) -> tuple[str, str]:
    if platform.system() == "Windows":
        return ("cmd.exe", "/c")
    return ("/bin/bash", "-c")
