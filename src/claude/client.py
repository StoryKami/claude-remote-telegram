from __future__ import annotations

import logging
from typing import TYPE_CHECKING, AsyncIterator

import anthropic

from src.claude.tools import get_tool_definitions

if TYPE_CHECKING:
    from src.claude.executor import ToolExecutor
    from src.config import Settings
    from src.session.manager import SessionManager

logger = logging.getLogger(__name__)


class ClaudeClient:
    def __init__(
        self,
        settings: Settings,
        session_manager: SessionManager,
        tool_executor: ToolExecutor,
    ) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._settings = settings
        self._session_manager = session_manager
        self._tool_executor = tool_executor
        self._tools = get_tool_definitions()

    async def send_message(
        self,
        session_id: str,
        user_message: str,
        model: str | None = None,
        system_prompt: str | None = None,
        on_tool_use: ToolUseCallback | None = None,
    ) -> str:
        await self._session_manager.add_message(session_id, "user", user_message)
        history = await self._session_manager.get_history(session_id)

        effective_model = model or self._settings.claude_model
        effective_system = system_prompt or self._settings.get_system_prompt()

        final_text = ""
        loops = 0

        while loops < self._settings.max_tool_loops:
            loops += 1

            response = await self._client.messages.create(
                model=effective_model,
                max_tokens=self._settings.claude_max_tokens,
                system=effective_system,
                tools=self._tools,
                messages=history,
            )

            text_parts: list[str] = []
            tool_uses: list[dict] = []

            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_uses.append({
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })

            if text_parts:
                final_text = "\n".join(text_parts)

            if not tool_uses:
                # Save assistant response and return
                assistant_content = _build_content_blocks(response.content)
                await self._session_manager.add_message(
                    session_id, "assistant", assistant_content
                )
                return final_text

            # Process tool uses
            assistant_content = _build_content_blocks(response.content)
            history.append({"role": "assistant", "content": assistant_content})

            tool_results = []
            for tool_use in tool_uses:
                if on_tool_use:
                    await on_tool_use(tool_use["name"], tool_use["input"])

                result = await self._tool_executor.execute(
                    tool_use["name"], tool_use["input"]
                )

                content = result.output if result.success else f"Error: {result.error}\n{result.output}"
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use["id"],
                    "content": content,
                })

            history.append({"role": "user", "content": tool_results})

        # Hit max loops
        if not final_text:
            final_text = "(Reached maximum tool execution limit)"

        await self._session_manager.add_message(session_id, "assistant", final_text)
        return final_text

    async def stream_message(
        self,
        session_id: str,
        user_message: str,
        model: str | None = None,
        system_prompt: str | None = None,
        on_tool_use: ToolUseCallback | None = None,
    ) -> AsyncIterator[StreamEvent]:
        await self._session_manager.add_message(session_id, "user", user_message)
        history = await self._session_manager.get_history(session_id)

        effective_model = model or self._settings.claude_model
        effective_system = system_prompt or self._settings.get_system_prompt()

        loops = 0
        final_text = ""

        while loops < self._settings.max_tool_loops:
            loops += 1

            collected_text = ""
            tool_uses: list[dict] = []
            current_tool: dict | None = None
            content_blocks: list[dict] = []

            async with self._client.messages.stream(
                model=effective_model,
                max_tokens=self._settings.claude_max_tokens,
                system=effective_system,
                tools=self._tools,
                messages=history,
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_start":
                        if event.content_block.type == "text":
                            pass
                        elif event.content_block.type == "tool_use":
                            current_tool = {
                                "id": event.content_block.id,
                                "name": event.content_block.name,
                                "input_json": "",
                            }
                    elif event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            collected_text += event.delta.text
                            yield StreamEvent("text", event.delta.text)
                        elif event.delta.type == "input_json_delta":
                            if current_tool:
                                current_tool["input_json"] += event.delta.partial_json
                    elif event.type == "content_block_stop":
                        if current_tool:
                            import json
                            try:
                                parsed_input = json.loads(current_tool["input_json"]) if current_tool["input_json"] else {}
                            except json.JSONDecodeError:
                                parsed_input = {}
                            tool_uses.append({
                                "id": current_tool["id"],
                                "name": current_tool["name"],
                                "input": parsed_input,
                            })
                            content_blocks.append({
                                "type": "tool_use",
                                "id": current_tool["id"],
                                "name": current_tool["name"],
                                "input": parsed_input,
                            })
                            current_tool = None
                        elif collected_text:
                            content_blocks.append({
                                "type": "text",
                                "text": collected_text,
                            })

            if collected_text:
                final_text = collected_text

            if not tool_uses:
                await self._session_manager.add_message(
                    session_id, "assistant", content_blocks or final_text
                )
                return

            # Process tool uses
            history.append({"role": "assistant", "content": content_blocks})
            tool_results = []
            for tool_use in tool_uses:
                yield StreamEvent("tool_use", f"Running: {tool_use['name']}")
                if on_tool_use:
                    await on_tool_use(tool_use["name"], tool_use["input"])

                result = await self._tool_executor.execute(
                    tool_use["name"], tool_use["input"]
                )

                content = result.output if result.success else f"Error: {result.error}\n{result.output}"
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use["id"],
                    "content": content,
                })
                yield StreamEvent("tool_result", f"{tool_use['name']}: {'OK' if result.success else 'FAIL'}")

            history.append({"role": "user", "content": tool_results})
            # Reset for next loop
            collected_text = ""
            content_blocks = []

        await self._session_manager.add_message(session_id, "assistant", final_text or "(max tool loops)")
        yield StreamEvent("text", "\n(Reached maximum tool execution limit)")


class StreamEvent:
    __slots__ = ("type", "data")

    def __init__(self, type: str, data: str) -> None:
        self.type = type
        self.data = data


# Type alias
type ToolUseCallback = object  # Actually: Callable[[str, dict], Awaitable[None]]


def _build_content_blocks(content: list) -> list[dict]:
    blocks = []
    for block in content:
        if block.type == "text":
            blocks.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            blocks.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
    return blocks
