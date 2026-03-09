from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import AsyncIterator

from src.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StreamEvent:
    type: str  # "text", "tool_use", "tool_result", "error", "done"
    data: str
    session_id: str | None = None


class ClaudeBridge:
    def __init__(self, settings: Settings) -> None:
        self._cli = settings.claude_cli_path
        self._model = settings.claude_model
        self._permission_mode = settings.claude_permission_mode
        self._max_budget = settings.claude_max_budget_usd
        self._workspace = settings.get_workspace_path()
        self._timeout = settings.cli_timeout

    def _build_command(
        self,
        prompt: str,
        claude_session_id: str | None = None,
    ) -> list[str]:
        cmd = [
            self._cli,
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", self._permission_mode,
        ]
        if self._model:
            cmd.extend(["--model", self._model])
        if self._max_budget > 0:
            cmd.extend(["--max-budget-usd", str(self._max_budget)])
        if claude_session_id:
            cmd.extend(["--resume", claude_session_id])
        cmd.append(prompt)
        return cmd

    async def send_message(
        self,
        prompt: str,
        claude_session_id: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        cmd = self._build_command(prompt, claude_session_id)
        logger.info("CLI command: %s", " ".join(cmd[:6]) + "...")

        # Remove CLAUDECODE env var to allow running inside a Claude Code session
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._workspace),
                env=env,
                limit=1024 * 1024,  # 1MB — handles large JSON lines from image reads
            )
        except FileNotFoundError:
            yield StreamEvent("error", f"Claude CLI not found: {self._cli}")
            return

        assert process.stdout

        result_session_id: str | None = None
        accumulated_text = ""

        try:
            async for raw_line in _read_lines_with_timeout(process.stdout, self._timeout):
                line = raw_line.strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("Non-JSON line: %s", line[:200])
                    continue

                parsed = _parse_event(event)
                if parsed:
                    if parsed.session_id:
                        result_session_id = parsed.session_id
                    if parsed.type == "text":
                        accumulated_text += parsed.data
                    yield parsed

        except asyncio.TimeoutError:
            process.kill()
            yield StreamEvent("error", f"CLI timed out after {self._timeout}s")
            return
        except Exception as e:
            logger.exception("Bridge error")
            yield StreamEvent("error", str(e))
            return

        # Wait for process to finish
        await process.wait()

        if process.returncode != 0 and not accumulated_text:
            stderr = ""
            if process.stderr:
                stderr_bytes = await process.stderr.read()
                stderr = stderr_bytes.decode("utf-8", errors="replace")
            yield StreamEvent("error", f"CLI exited with code {process.returncode}: {stderr[:500]}")

        yield StreamEvent("done", accumulated_text, session_id=result_session_id)


def _parse_event(event: dict) -> StreamEvent | None:
    """Parse a stream-json event from Claude CLI."""
    event_type = event.get("type", "")

    # assistant message events
    if event_type == "assistant":
        subtype = event.get("subtype", "")
        if subtype == "text":
            return StreamEvent("text", event.get("text", ""))
        if subtype == "tool_use":
            tool = event.get("tool", {})
            name = tool.get("name", "unknown") if isinstance(tool, dict) else str(tool)
            tool_input = tool.get("input", {}) if isinstance(tool, dict) else {}
            desc = _describe_tool(name, tool_input)
            return StreamEvent("tool_use", desc)
        # Check for thinking in message content
        msg = event.get("message", {})
        if isinstance(msg, dict):
            for block in msg.get("content", []):
                if isinstance(block, dict) and block.get("type") == "thinking":
                    thinking = block.get("thinking", "")
                    if thinking:
                        return StreamEvent("thinking", thinking)

    # tool result events
    if event_type == "tool":
        subtype = event.get("subtype", "")
        if subtype == "result":
            content = event.get("content", "")
            if isinstance(content, list):
                content = "\n".join(
                    block.get("text", "") for block in content if isinstance(block, dict)
                )
            return StreamEvent("tool_result", str(content)[:200])

    # content_block_delta (alternative format)
    if event_type == "content_block_delta":
        delta = event.get("delta", {})
        if delta.get("type") == "text_delta":
            return StreamEvent("text", delta.get("text", ""))
        if delta.get("type") == "thinking_delta":
            return StreamEvent("thinking", delta.get("thinking", ""))

    # result event
    if event_type == "result":
        session_id = event.get("session_id", "")
        result_text = event.get("result", "")
        cost = event.get("cost_usd")
        data = result_text
        if cost:
            data += f"\n\n[Cost: ${cost:.4f}]"
        return StreamEvent("done", data, session_id=session_id)

    # message event (wraps content blocks)
    if event_type == "message":
        content = event.get("content", [])
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        if texts:
            return StreamEvent("text", "\n".join(texts))

    return None


def _describe_tool(name: str, params: dict) -> str:
    match name:
        case "Bash" | "bash":
            cmd = str(params.get("command", ""))[:60]
            return f"[bash] {cmd}"
        case "Read" | "read_file":
            return f"[read] {params.get('file_path', params.get('path', '?'))}"
        case "Write" | "write_file":
            return f"[write] {params.get('file_path', params.get('path', '?'))}"
        case "Edit":
            return f"[edit] {params.get('file_path', '?')}"
        case "Glob":
            return f"[glob] {params.get('pattern', '?')}"
        case "Grep":
            return f"[grep] {params.get('pattern', '?')}"
        case "Agent":
            return f"[agent] {params.get('description', '?')}"
        case _:
            return f"[{name}]"


async def _read_lines_with_timeout(
    stream: asyncio.StreamReader,
    timeout: int,
) -> AsyncIterator[str]:
    use_timeout = timeout > 0
    deadline = asyncio.get_event_loop().time() + timeout if use_timeout else 0
    while True:
        if use_timeout:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            line = await asyncio.wait_for(stream.readline(), timeout=remaining)
        else:
            line = await stream.readline()
        if not line:
            break
        yield line.decode("utf-8", errors="replace")
