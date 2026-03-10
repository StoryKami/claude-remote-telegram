from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import AsyncIterator, Callable, Awaitable

from claude_code_sdk import (
    ClaudeCodeOptions,
    AssistantMessage,
    ResultMessage,
    UserMessage,
    PermissionResultAllow,
    PermissionResultDeny,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    query,
)

from src.config import Settings

logger = logging.getLogger(__name__)

# Type for permission callback: (tool_name, params) -> bool
PermissionCallback = Callable[[str, dict], Awaitable[bool]]


@dataclass(frozen=True)
class StreamEvent:
    type: str  # "text", "thinking", "tool_use", "tool_result", "error", "done"
    data: str
    session_id: str | None = None


class ClaudeBridge:
    def __init__(self, settings: Settings) -> None:
        self._model = settings.claude_model or None
        self._default_permission_mode = settings.claude_permission_mode
        self._workspace = settings.get_workspace_path()
        # For kill support: session_id -> cancel event
        self._cancel_events: dict[str, asyncio.Event] = {}

    def request_cancel(self, key: str) -> None:
        """Signal cancellation for a running session."""
        evt = self._cancel_events.get(key)
        if evt:
            evt.set()
            logger.info("Cancel requested for key=%s", key)

    async def send_message(
        self,
        prompt: str,
        claude_session_id: str | None = None,
        process_key: str | None = None,
        permission_mode: str | None = None,
        permission_callback: PermissionCallback | None = None,
    ) -> AsyncIterator[StreamEvent]:
        mode = permission_mode or self._default_permission_mode

        # Build SDK options
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        async def can_use_tool(tool_name: str, params: dict, context: object) -> PermissionResultAllow | PermissionResultDeny:
            if permission_callback:
                approved = await permission_callback(tool_name, params)
                if approved:
                    return PermissionResultAllow(behavior="allow", updated_input=None, updated_permissions=None)
                return PermissionResultDeny(behavior="deny", message="User denied via Telegram", interrupt=False)
            # No callback = auto-allow (bypassPermissions behavior)
            return PermissionResultAllow(behavior="allow", updated_input=None, updated_permissions=None)

        options = ClaudeCodeOptions(
            permission_mode=mode if mode in ("default", "acceptEdits", "plan", "bypassPermissions") else "bypassPermissions",
            cwd=str(self._workspace),
            env=env,
            can_use_tool=can_use_tool if permission_callback or mode == "default" else None,
        )
        if self._model:
            options.model = self._model
        if claude_session_id:
            options.resume = claude_session_id

        # Set up cancel event
        cancel_event = asyncio.Event()
        if process_key:
            self._cancel_events[process_key] = cancel_event

        result_session_id: str | None = None
        accumulated_text = ""

        logger.info("SDK query: mode=%s resume=%s", mode, claude_session_id)

        cancelled = False

        try:
            async for message in query(prompt=prompt, options=options):
                # Check cancel — don't break, just stop yielding
                if cancel_event.is_set():
                    if not cancelled:
                        logger.info("Query cancelled for key=%s", process_key)
                        cancelled = True
                    continue

                logger.debug("SDK message: %s blocks=%s",
                    type(message).__name__,
                    [type(b).__name__ for b in message.content] if hasattr(message, 'content') else "N/A",
                )

                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            accumulated_text += block.text
                            logger.debug("TextBlock: %s", block.text[:100])
                            yield StreamEvent("text", block.text)
                        elif isinstance(block, ThinkingBlock):
                            if block.thinking:
                                yield StreamEvent("thinking", block.thinking)
                        elif isinstance(block, ToolUseBlock):
                            desc = _describe_tool(block.name, block.input if isinstance(block.input, dict) else {})
                            logger.debug("tool_use: %s", desc)
                            yield StreamEvent("tool_use", desc)
                        elif isinstance(block, ToolResultBlock):
                            content = block.content if isinstance(block.content, str) else str(block.content)[:200]
                            yield StreamEvent("tool_result", content)
                        else:
                            logger.debug("Unknown block: %s", type(block).__name__)

                elif isinstance(message, UserMessage):
                    yield StreamEvent("tool_result", "")

                elif isinstance(message, ResultMessage):
                    result_session_id = message.session_id
                    cost = getattr(message, "cost_usd", None) or getattr(message, "total_cost_usd", None)
                    if cost:
                        accumulated_text += f"\n\n[Cost: ${cost:.4f}]"

        except GeneratorExit:
            pass
        except RuntimeError as e:
            if "cancel scope" in str(e):
                logger.warning("SDK cancel scope issue (suppressed): %s", e)
            else:
                logger.exception("SDK runtime error")
                yield StreamEvent("error", str(e))
                return
        except Exception as e:
            logger.exception("SDK query error")
            yield StreamEvent("error", str(e))
            return
        finally:
            if process_key:
                self._cancel_events.pop(process_key, None)

        yield StreamEvent("done", accumulated_text, session_id=result_session_id)


def _short_path(path: str) -> str:
    """Shorten a file path to just filename or last 2 segments."""
    try:
        p = PureWindowsPath(path) if "\\" in path else PurePosixPath(path)
        parts = p.parts
        if len(parts) <= 2:
            return str(p)
        return str(PurePosixPath(*parts[-2:]))
    except Exception:
        return path


def _short_bash(cmd: str) -> str:
    """Extract the meaningful part of a bash command."""
    for prefix in ['wsl -d Ubuntu -e bash -c "', "wsl -d Ubuntu -e bash -c '"]:
        if cmd.startswith(prefix):
            cmd = cmd[len(prefix):].rstrip("\"'")
    if "ssh " in cmd and "bash -c" in cmd:
        idx = cmd.find("bash -c")
        if idx >= 0:
            cmd = cmd[idx + 7:].strip().strip("\"'\\")
    if len(cmd) > 40:
        cmd = cmd[:37] + "..."
    return cmd


def _describe_tool(name: str, params: dict) -> str:
    match name:
        case "Bash" | "bash":
            cmd = _short_bash(str(params.get("command", "")))
            return f"bash: {cmd}"
        case "Read" | "read_file":
            path = _short_path(params.get("file_path", params.get("path", "?")))
            return f"read: {path}"
        case "Write" | "write_file":
            path = _short_path(params.get("file_path", params.get("path", "?")))
            return f"write: {path}"
        case "Edit":
            path = _short_path(params.get("file_path", "?"))
            return f"edit: {path}"
        case "Glob":
            return f"glob: {params.get('pattern', '?')}"
        case "Grep":
            return f"grep: {params.get('pattern', '?')}"
        case "Agent":
            return f"agent: {params.get('description', '?')}"
        case "WebSearch":
            return f"search: {params.get('query', '?')[:40]}"
        case _:
            return name.lower()
