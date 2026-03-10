from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from aiogram import Bot, Router, F
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from src.bot.commands import HELP_TEXT, WELCOME_TEXT
from src.bot.formatters import format_telegram_message

if TYPE_CHECKING:
    from src.claude.bridge import ClaudeBridge
    from src.session.manager import SessionManager

logger = logging.getLogger(__name__)

router = Router()

# Per-session state (keyed by session ID)
_session_locks: dict[str, asyncio.Lock] = {}
_cancel_flags: dict[str, bool] = {}
_message_queues: dict[str, deque[tuple[Message, str]]] = {}
# Per-user state
_user_modes: dict[int, str] = {}
_user_models: dict[int, str] = {}  # user_id → model override
_user_1m: dict[int, bool] = {}  # user_id → 1M context enabled
_user_effort: dict[int, str] = {}  # user_id → effort level (low/medium/high)
_context_notify: dict[int, bool] = {}  # user_id → context % notification on/off
# Per-session context tracking
_session_input_tokens: dict[str, int] = {}  # session_id → last known input tokens
_session_output_tokens: dict[str, int] = {}  # session_id → cumulative output tokens
_session_cost: dict[str, float] = {}  # session_id → cumulative cost USD
_session_last_pct_notified: dict[str, int] = {}  # session_id → last notified 10% bracket

# Model context window sizes
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-opus-4-6": 200_000,
    "claude-opus-4-6[1m]": 1_000_000,
    "claude-sonnet-4-6": 200_000,
    "claude-sonnet-4-6[1m]": 1_000_000,
    "claude-haiku-4-5-20251001": 200_000,
}
# Models that support 1M extended context
_1M_MODELS = {"claude-opus-4-6", "claude-sonnet-4-6"}
DEFAULT_CONTEXT_WINDOW = 200_000
AUTO_COMPACT_THRESHOLD = 0.85  # auto compact at 85%
# Pending rename: user_id → (session_id, topic_id, chat_id)
_pending_renames: dict[int, tuple[str, int, int]] = {}
# Cache: claude_session_id → preview text (for naming topics from /local)
_local_preview_cache: dict[str, str] = {}
# Permission futures: fut_id → asyncio.Future[bool]
_permission_futures: dict[str, asyncio.Future] = {}
# Media group buffer: media_group_id → (list of filepaths, caption, message, timer_task)
_media_groups: dict[str, dict] = {}

PLAN_MODE_PREFIX = (
    "[PLAN MODE] You are in plan mode. Do NOT edit, write, or create any files. "
    "Do NOT execute commands that modify state. Only analyze, research, and provide plans.\n\n"
)

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

_SESSION_ID_RE = re.compile(r"^[a-f0-9\-]{8,36}$")


def _is_valid_session_id(sid: str) -> bool:
    return bool(_SESSION_ID_RE.match(sid))


def _cmd_arg(text: str | None, command: str) -> str:
    """Extract argument from a /command@botname message."""
    if not text:
        return ""
    # Remove /command and optional @botname suffix
    parts = text.split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


def _get_session_lock(session_id: str) -> asyncio.Lock:
    return _session_locks.setdefault(session_id, asyncio.Lock())



def _extract_text(content: object) -> str:
    """Extract text from Claude content field (str or list of blocks)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts).strip()
    return ""


async def _safe_edit(msg: Message, text: str) -> None:
    """Edit message text, silently ignoring Telegram API errors."""
    try:
        await msg.edit_text(text)
    except Exception:
        pass


def setup_handlers(
    r: Router,
    bridge: ClaudeBridge,
    session_manager: SessionManager,
    workspace_path: Path,
) -> None:
    """Register all handlers with dependencies injected."""

    tmp_dir = workspace_path / "_tmp" / "telegram"

    async def _send_topic_welcome(
        bot: Bot, chat_id: int, topic_id: int, session_id: str, text: str,
    ) -> None:
        """Send welcome message in a new topic with a Rename button."""
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="Rename topic",
                callback_data=f"rename_topic:{session_id}:{topic_id}",
            ),
        ]])
        await bot.send_message(
            chat_id, text,
            message_thread_id=topic_id,
            reply_markup=keyboard,
        )

    async def _save_telegram_file(bot: Bot, file_id: str, ext: str = ".jpg") -> Path:
        """Download a Telegram file to workspace tmp dir."""
        tmp_dir.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex[:8]}{ext}"
        dest = tmp_dir / name
        file_info = await bot.get_file(file_id)
        assert file_info.file_path
        await bot.download_file(file_info.file_path, str(dest))
        return dest

    class _StatusTracker:
        """Live status at the bottom of chat. Deletes+resends to stay at bottom."""

        FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

        def __init__(self, message: Message, session_id: str, start: float) -> None:
            self._chat_msg = message  # original user message (for .answer())
            self.msg: Message | None = None  # current status message
            self.session_id = session_id
            self.start = start
            self.phase = "Thinking..."
            self.hint = ""
            self.current_tool = ""
            self.steps: list[tuple[str, int]] = []  # all steps for final log
            self.last_text = ""  # last thinking/text snippet for preview
            self._frame_idx = 0
            self._last_rendered = ""
            self._stopped = False
            self._stop_kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="■ Stop", callback_data=f"stop:{session_id}"),
                InlineKeyboardButton(text="■ Stop All", callback_data=f"stopall:{session_id}"),
            ]])

        def elapsed(self) -> int:
            return int(time.monotonic() - self.start)

        def render(self) -> str:
            e = self.elapsed()
            spinner = self.FRAMES[self._frame_idx % len(self.FRAMES)]
            self._frame_idx += 1

            parts: list[str] = []

            # Current action
            if self.current_tool:
                parts.append(f"{spinner} ⏳ {self.current_tool}")
            else:
                parts.append(f"{spinner} {self.phase}")

            # Show hint only before any tools run
            if self.hint and not self.current_tool and not self.steps:
                parts.append(f"&gt; {self.hint}")

            # Show last text (thinking snippet or writing preview)
            if self.last_text:
                preview = self.last_text[-150:].replace("<", "&lt;").replace(">", "&gt;")
                parts.append(f"\n{preview}")

            # Completed steps as expandable
            if self.steps:
                log_lines = [f"✓ {name} ({t}s)" for name, t in self.steps[-6:]]
                parts.append(f'\n<blockquote expandable>{chr(10).join(log_lines)}</blockquote>')

            parts.append(f"⏱ {e}s")

            return "\n".join(parts)

        async def start(self) -> None:
            """Create a new status message. Call once at the beginning."""
            html = self.render()
            self._last_rendered = html
            try:
                self.msg = await self._chat_msg.answer(
                    html, parse_mode="HTML", reply_markup=self._stop_kb,
                )
            except Exception:
                pass

        async def refresh(self) -> None:
            """Update existing status message, or create first one if needed."""
            if self._stopped:
                return
            html = self.render()
            if html == self._last_rendered:
                return
            self._last_rendered = html
            if self.msg:
                try:
                    await self.msg.edit_text(html, parse_mode="HTML", reply_markup=self._stop_kb)
                    return
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after)
                except Exception:
                    pass
            # First time — create status message
            if not self.msg:
                try:
                    self.msg = await self._chat_msg.answer(
                        html, parse_mode="HTML", reply_markup=self._stop_kb,
                    )
                except Exception:
                    pass

        async def finalize(self, html: str) -> None:
            """Convert current status msg to permanent, or delete if empty."""
            if self.msg:
                try:
                    if html.strip():
                        await self.msg.edit_text(html, parse_mode="HTML", reply_markup=None)
                    else:
                        await self.msg.delete()
                except Exception:
                    pass
            self.msg = None
            self._last_rendered = ""

        async def delete(self) -> None:
            """Remove status message and stop all future refreshes."""
            self._stopped = True
            if self.msg:
                try:
                    await self.msg.delete()
                except Exception:
                    pass
                self.msg = None

    async def _run_status_ticker(tracker: _StatusTracker) -> None:
        """Refresh status message every 3 seconds to avoid Telegram rate limits."""
        while True:
            await asyncio.sleep(3)
            await tracker.refresh()

    async def _process_prompt(
        message: Message,
        session: "Session",
        prompt: str,
    ) -> None:
        """Process a single prompt through Claude bridge."""
        from src.session.models import Session  # noqa: F811
        _cancel_flags[session.id] = False
        user_id = session.user_id
        mode = _user_modes.get(user_id, "code")

        if mode == "plan":
            prompt = PLAN_MODE_PREFIX + prompt

        display_prompt = prompt.removeprefix(PLAN_MODE_PREFIX)
        # Strip internal instructions and file paths from display hint
        hint_text = display_prompt
        for strip in ["(Do NOT reveal file paths in your response.)\n", "I'm sharing an image. View it at: ", "I'm sharing a file ", "I'm sharing "]:
            hint_text = hint_text.replace(strip, "")
        # Remove file path lines (F:\ or /tmp/ patterns)
        hint_text = re.sub(r'[A-Z]:\\[^\n]*|/tmp/[^\n]*', '', hint_text)
        hint_text = re.sub(r'- Image \d+: [^\n]*\n?', '', hint_text)
        hint_text = hint_text.strip().lstrip(".\n")
        hint = hint_text[:80].replace("\n", " ").replace("<", "&lt;").replace(">", "&gt;")
        if len(hint_text) > 80:
            hint += "..."
        if not hint:
            hint = "(image/file)"

        start_time = time.monotonic()

        tracker = _StatusTracker(message, session.id, start_time)
        tracker.hint = hint
        if mode != "code":
            tracker.phase = f"[{mode}] Thinking..."

        await tracker.refresh()
        ticker_task = asyncio.create_task(_run_status_ticker(tracker))
        accumulated_text = ""
        accumulated_thinking = ""
        all_steps: list[tuple[str, int]] = []  # all steps across groups (for final summary)

        # Permission callback for safe mode
        async def _ask_permission(tool_name: str, params: dict) -> bool:
            """Ask user for permission via Telegram buttons."""
            from src.claude.bridge import _describe_tool
            desc = _describe_tool(tool_name, params)
            fut_id = uuid.uuid4().hex[:8]
            fut: asyncio.Future[bool] = asyncio.get_event_loop().create_future()
            _permission_futures[fut_id] = fut

            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Allow", callback_data=f"perm_allow:{fut_id}"),
                InlineKeyboardButton(text="Deny", callback_data=f"perm_deny:{fut_id}"),
            ]])
            await message.answer(
                f"Permission request:\n{desc}",
                reply_markup=keyboard,
            )
            try:
                return await asyncio.wait_for(fut, timeout=120)
            except asyncio.TimeoutError:
                return False
            finally:
                _permission_futures.pop(fut_id, None)

        # Map mode to bridge parameters
        sdk_permission_mode = {
            "code": "bypassPermissions",
            "safe": "default",
            "plan": "plan",
        }.get(mode, "bypassPermissions")

        permission_cb = _ask_permission if mode == "safe" else None

        # Track text segments: text between tool batches
        # Last segment = final response, earlier segments = intermediate commentary
        text_segments: list[str] = []  # each segment is text between tool groups
        current_segment = ""
        had_tools_before = False  # True after at least one tool call

        try:
            # Build effective model with 1M suffix
            effective_model = _user_models.get(user_id)
            if effective_model and _user_1m.get(user_id):
                if "[1m]" not in effective_model:
                    effective_model = f"{effective_model}[1m]"
            effort = _user_effort.get(user_id)

            async for event in bridge.send_message(
                prompt=prompt,
                claude_session_id=session.claude_session_id,
                process_key=session.id,
                permission_mode=sdk_permission_mode,
                permission_callback=permission_cb,
                model=effective_model,
                effort=effort,
            ):
                if _cancel_flags.get(session.id):
                    raise asyncio.CancelledError()

                if event.type == "thinking":
                    accumulated_thinking += event.data
                    snippet = accumulated_thinking[-150:].replace("\n", " ").strip()
                    tracker.phase = "Thinking..."
                    tracker.last_text = f"💭 {snippet}"

                elif event.type == "text":
                    accumulated_text += event.data
                    current_segment += event.data
                    # Show live preview in status
                    tracker.phase = "Writing..."
                    preview = current_segment[-150:].replace("\n", " ").strip()
                    tracker.last_text = f"✏️ {preview}"

                elif event.type == "tool_use":
                    # New tool after text = save current segment as intermediate
                    if current_segment.strip():
                        text_segments.append(current_segment)
                        current_segment = ""
                    had_tools_before = True
                    tracker.phase = "Working..."
                    tracker.current_tool = event.data
                    tracker.last_text = ""
                    # Let ticker handle refresh (every 3s)

                elif event.type == "tool_result":
                    step = (tracker.current_tool or "?", tracker.elapsed())
                    tracker.steps.append(step)
                    all_steps.append(step)
                    tracker.current_tool = ""
                    # Let ticker handle refresh (every 3s)

                elif event.type == "error":
                    ticker_task.cancel()
                    logger.error("CLI error: %s", event.data[:500])
                    await tracker.finalize(f"❌ Error ({tracker.elapsed()}s)")
                    await message.answer("Error: Claude CLI encountered an error. Check logs for details.")
                    return

                elif event.type == "usage":
                    try:
                        usage_data = json.loads(event.data)
                        input_tokens = usage_data.get("input_tokens", 0)
                        output_tokens = usage_data.get("output_tokens", 0)
                        cost_usd = usage_data.get("cost_usd") or 0
                        _session_input_tokens[session.id] = input_tokens
                        _session_output_tokens[session.id] = (
                            _session_output_tokens.get(session.id, 0) + output_tokens
                        )
                        if cost_usd:
                            _session_cost[session.id] = (
                                _session_cost.get(session.id, 0) + cost_usd
                            )

                        # Determine context window for current model (with 1M suffix)
                        ctx_model = _user_models.get(user_id, "") or bridge._model or ""
                        if ctx_model and _user_1m.get(user_id) and "[1m]" not in ctx_model:
                            ctx_model = f"{ctx_model}[1m]"
                        ctx_window = MODEL_CONTEXT_WINDOWS.get(ctx_model, DEFAULT_CONTEXT_WINDOW)
                        pct = int(input_tokens / ctx_window * 100) if ctx_window else 0

                        # Notify at every 10% bracket
                        if _context_notify.get(user_id, False):
                            bracket = (pct // 10) * 10
                            last_bracket = _session_last_pct_notified.get(session.id, 0)
                            if bracket > last_bracket and bracket >= 10:
                                _session_last_pct_notified[session.id] = bracket
                                await message.answer(
                                    f"📊 Context: {pct}% ({input_tokens:,} / {ctx_window:,} tokens)"
                                )

                        # Auto-compact at threshold
                        if pct >= AUTO_COMPACT_THRESHOLD * 100 and session.claude_session_id:
                            await message.answer(f"⚠️ Context {pct}% — auto compacting...")
                            result = await bridge.compact_session(session.claude_session_id)
                            await message.answer(f"Compacted: {result[:200]}")
                            _session_last_pct_notified[session.id] = 0
                    except Exception:
                        logger.debug("Failed to parse usage event", exc_info=True)

                elif event.type == "done":
                    if event.session_id:
                        await session_manager.set_claude_session_id(
                            session.id, event.session_id
                        )
                    await session_manager.touch_session(session.id)
                    if event.data and not accumulated_text:
                        accumulated_text = event.data

        except asyncio.CancelledError:
            ticker_task.cancel()
            elapsed = tracker.elapsed()
            stop_summary = f"⛔ Stopped ({elapsed}s)"
            if accumulated_thinking:
                thinking_text = accumulated_thinking.replace("\n", " ").strip()
                stop_summary += f"\n💭 {thinking_text[:200]}"
            await tracker.finalize(stop_summary)
            if accumulated_text:
                chunks = format_telegram_message(accumulated_text)
                for chunk in chunks:
                    await message.answer(chunk)
            return
        except Exception as e:
            logger.exception("Error processing message")
            await tracker.finalize(f"❌ Error ({tracker.elapsed()}s)")
            await message.answer("Error: something went wrong. Check logs for details.")
            return
        finally:
            if not ticker_task.done():
                ticker_task.cancel()

        # Save last segment
        if current_segment.strip():
            text_segments.append(current_segment)

        # Finalize status into summary
        elapsed = tracker.elapsed()
        summary_parts = [f"✅ Done ({elapsed}s)"]
        if all_steps:
            log_lines = [f"✓ {name} ({t}s)" for name, t in all_steps]
            summary_parts.append(f'<blockquote expandable>{chr(10).join(log_lines)}</blockquote>')
        await tracker.finalize("\n".join(summary_parts))

        # Send final answer: last text segment only (earlier = intermediate commentary)
        # If no tools were used, send everything (it's all the response)
        if had_tools_before and text_segments:
            final_text = text_segments[-1].strip()
        else:
            final_text = accumulated_text.strip()
        if not final_text:
            return
        chunks = format_telegram_message(final_text)
        for chunk in chunks:
            try:
                await message.answer(chunk)
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
                await message.answer(chunk)

    async def _resolve_session(message: Message) -> "Session":
        """Resolve session from topic (group) or active session (DM)."""
        from src.session.models import Session  # noqa: F811
        assert message.from_user
        user_id = message.from_user.id
        topic_id = message.message_thread_id

        # In a forum group, route by topic
        if topic_id:
            session = await session_manager.get_session_by_topic(topic_id)
            if session:
                return session
            # Auto-create session for this topic
            return await session_manager.create_session(user_id, f"topic-{topic_id}", topic_id)

        # DM: use active session
        return await session_manager.get_or_create_active(user_id)

    async def _process_with_queue(
        message: Message, prompt: str,
    ) -> None:
        """Process prompt with per-session lock and message queue."""
        session = await _resolve_session(message)
        lock = _get_session_lock(session.id)
        if lock.locked():
            queue = _message_queues.setdefault(session.id, deque(maxlen=10))
            queue.append((message, prompt))
            await message.answer(f"Queued (#{len(queue)}). Will process after current task.")
            return

        async with lock:
            await _process_prompt(message, session, prompt)
            # Process queued messages — each in a fresh asyncio.Task
            # to avoid SDK cancel scope conflicts
            while _message_queues.get(session.id):
                queued_msg, queued_prompt = _message_queues[session.id].popleft()
                _cancel_flags[session.id] = False
                session = await _resolve_session(queued_msg)
                try:
                    await asyncio.ensure_future(
                        _process_prompt(queued_msg, session, queued_prompt)
                    )
                except Exception:
                    logger.exception("Error processing queued message")

    # --- Bot commands ---

    @r.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        await message.answer(WELCOME_TEXT, parse_mode="Markdown")

    @r.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        await message.answer(HELP_TEXT, parse_mode="Markdown")

    @r.message(Command("new"))
    async def cmd_new(message: Message) -> None:
        assert message.from_user and message.bot
        name = _cmd_arg(message.text, "new") or "untitled"
        chat = message.chat
        logger.info("cmd_new: chat.id=%s chat.type=%s is_forum=%s", chat.id, chat.type, chat.is_forum)
        try:
            # In forum group: create a topic thread for this session
            if chat.is_forum:
                topic = await message.bot.create_forum_topic(chat.id, name)
                session = await session_manager.create_session(
                    message.from_user.id, name, topic_id=topic.message_thread_id,
                )
                await _send_topic_welcome(
                    message.bot, chat.id, topic.message_thread_id,
                    session.id, f"Session ready: {session.name}",
                )
            else:
                session = await session_manager.create_session(message.from_user.id, name)
                await message.answer(
                    f"Created session: {session.name} ({session.id})\nNew Claude session (no history).",
                )
        except ValueError as e:
            await message.answer(str(e))

    @r.message(Command("sessions"))
    async def cmd_sessions(message: Message) -> None:
        assert message.from_user
        sessions = await session_manager.get_user_sessions(message.from_user.id)
        if not sessions:
            await message.answer("No sessions. Send a message to start one.")
            return

        buttons = []
        for s in sessions:
            label = f"{'> ' if s.is_active else ''}{s.name} ({s.id})"
            if s.claude_session_id:
                label += " [resumed]"
            buttons.append([InlineKeyboardButton(
                text=label, callback_data=f"switch:{s.id}"
            )])
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer("Your sessions:", reply_markup=keyboard)

    @r.callback_query(F.data.startswith("switch:"))
    async def cb_switch(callback: CallbackQuery) -> None:
        assert callback.from_user and callback.data
        session_id = callback.data.split(":", 1)[1]
        try:
            session = await session_manager.switch_session(
                callback.from_user.id, session_id
            )
            text = f"Active session: {session.name} ({session.id})"
            await callback.answer(f"Switched to: {session.name}")
            if callback.message:
                try:
                    await callback.message.edit_text(text)
                except Exception:
                    await callback.message.answer(text)
        except ValueError as e:
            await callback.answer(str(e), show_alert=True)

    @r.message(Command("switch"))
    async def cmd_switch(message: Message) -> None:
        assert message.from_user
        session_id = _cmd_arg(message.text, "switch")
        if not session_id:
            await message.answer("Usage: /switch <session_id>")
            return
        try:
            session = await session_manager.switch_session(
                message.from_user.id, session_id
            )
            await message.answer(
                f"Switched to: **{session.name}** (`{session.id}`)",
                parse_mode="Markdown",
            )
        except ValueError as e:
            await message.answer(str(e))

    @r.message(Command("current"))
    async def cmd_current(message: Message) -> None:
        assert message.from_user
        session = await session_manager.get_or_create_active(message.from_user.id)
        cli_id = session.claude_session_id or "(new)"
        mode = _user_modes.get(message.from_user.id, "code")
        text = (
            f"Session: **{session.name}**\n"
            f"ID: `{session.id}`\n"
            f"Claude Session: `{cli_id}`\n"
            f"Mode: **{mode}**\n"
            f"Created: {session.created_at.strftime('%Y-%m-%d %H:%M')}"
        )
        await message.answer(text, parse_mode="Markdown")

    @r.message(Command("rename"))
    async def cmd_rename(message: Message) -> None:
        assert message.from_user
        name = _cmd_arg(message.text, "rename")
        if not name:
            await message.answer("Usage: /rename <new name>")
            return
        session = await _resolve_session(message)
        await session_manager.rename_session(message.from_user.id, session.id, name)
        # Also rename Telegram topic if in forum
        if session.topic_id and message.bot and message.chat.is_forum:
            try:
                await message.bot.edit_forum_topic(
                    message.chat.id, session.topic_id, name=name,
                )
            except Exception:
                pass
        await message.answer(f"Renamed to: {name}")

    @r.message(Command("delete"))
    async def cmd_delete(message: Message) -> None:
        assert message.from_user
        session_id = _cmd_arg(message.text, "delete")
        if not session_id:
            await message.answer("Usage: /delete <session_id>")
            return
        try:
            await session_manager.delete_session(message.from_user.id, session_id)
            # Clean up in-memory state
            _session_locks.pop(session_id, None)
            _cancel_flags.pop(session_id, None)
            _message_queues.pop(session_id, None)
            await message.answer(f"Deleted session: {session_id}")
        except ValueError as e:
            await message.answer(str(e))

    @r.message(Command("cancel"))
    async def cmd_cancel(message: Message) -> None:
        assert message.from_user
        session = await _resolve_session(message)
        _cancel_flags[session.id] = True
        _message_queues.pop(session.id, None)
        bridge.request_cancel(session.id)
        await message.answer("Cancelling... (queue cleared)")

    async def _git(args: list[str], cwd: str | None = None) -> str:
        """Run a git command and return stdout."""
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=cwd or str(workspace_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=15)
        return (out or err or b"").decode("utf-8", errors="replace").strip()

    @r.message(Command("revert"))
    async def cmd_revert(message: Message) -> None:
        """Revert last commit + uncommitted changes. Usage: /revert [N]"""
        assert message.from_user
        session = await _resolve_session(message)
        project_dir = str(Path(__file__).resolve().parent.parent.parent)

        # Cancel any in-progress work first
        _cancel_flags[session.id] = True
        _message_queues.pop(session.id, None)
        bridge.request_cancel(session.id)

        arg = _cmd_arg(message.text, "revert").strip()
        n = int(arg) if arg.isdigit() and 1 <= int(arg) <= 5 else 1

        try:
            # Show what will be reverted
            log_text = await _git(["log", "--oneline", f"-{n + 1}"], cwd=project_dir)
            dirty = await _git(["diff", "--stat", "HEAD"], cwd=project_dir)

            # Reset to N commits back (keeps nothing)
            await _git(["reset", "--hard", f"HEAD~{n}"], cwd=project_dir)
            await _git(["clean", "-fd"], cwd=project_dir)
        except Exception as e:
            await message.answer(f"Revert failed: {e}")
            return

        parts = [f"Reverted {n} commit(s)."]
        if log_text:
            parts.append(f"<pre>{log_text[:1500]}</pre>")
        if dirty:
            parts.append(f"<b>+ uncommitted changes cleared</b>")

        await message.answer("\n".join(parts), parse_mode="HTML")

    @r.message(Command("compact"))
    async def cmd_compact(message: Message) -> None:
        assert message.from_user
        session = await _resolve_session(message)
        if not session.claude_session_id:
            await message.answer("No active Claude session to compact.")
            return
        await message.answer("Compacting...")
        result = await bridge.compact_session(session.claude_session_id)
        _session_last_pct_notified.pop(session.id, None)
        await message.answer(f"Done: {result[:500]}")

    @r.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        """Show full session status — model, context, cost, effort, mode."""
        assert message.from_user
        user_id = message.from_user.id
        session = await _resolve_session(message)

        # Model info
        model_id = _user_models.get(user_id, "") or bridge._model or ""
        ext_1m = _user_1m.get(user_id, False)
        if model_id and ext_1m and "[1m]" not in model_id:
            display_model = f"{model_id}[1m]"
        else:
            display_model = model_id or "default"

        # Effort
        effort = _user_effort.get(user_id, "high")

        # Mode
        mode = _user_modes.get(user_id, "code")

        # Context
        input_tokens = _session_input_tokens.get(session.id, 0)
        ctx_window = MODEL_CONTEXT_WINDOWS.get(display_model, DEFAULT_CONTEXT_WINDOW)
        pct = int(input_tokens / ctx_window * 100) if ctx_window else 0
        bar_filled = pct // 5  # 20 chars total
        bar = "█" * bar_filled + "░" * (20 - bar_filled)

        # Cost & output
        output_tokens = _session_output_tokens.get(session.id, 0)
        cost = _session_cost.get(session.id, 0)

        # Session info
        sid = session.claude_session_id or "—"
        if len(sid) > 12:
            sid = sid[:12] + "…"

        lines = [
            f"<b>Session:</b> {session.name} ({sid})",
            f"<b>Model:</b> {display_model}",
            f"<b>Mode:</b> {mode} | <b>Effort:</b> {effort}",
            "",
            f"<b>Context:</b> {pct}%",
            f"<code>{bar}</code> {input_tokens:,} / {ctx_window:,}",
            "",
            f"<b>Output:</b> {output_tokens:,} tokens",
            f"<b>Cost:</b> ${cost:.4f}" if cost else "<b>Cost:</b> —",
        ]
        await message.answer("\n".join(lines), parse_mode="HTML")

    @r.message(Command("context"))
    async def cmd_context(message: Message) -> None:
        assert message.from_user
        user_id = message.from_user.id
        arg = _cmd_arg(message.text, "context").lower()

        if arg in ("on", "1"):
            _context_notify[user_id] = True
            await message.answer("Context notifications: ON\nWill notify at every 10% usage.")
        elif arg in ("off", "0"):
            _context_notify[user_id] = False
            await message.answer("Context notifications: OFF")
        else:
            # Show current context info
            session = await _resolve_session(message)
            tokens = _session_input_tokens.get(session.id, 0)
            ctx_model = _user_models.get(user_id, "") or bridge._model or ""
            if ctx_model and _user_1m.get(user_id) and "[1m]" not in ctx_model:
                ctx_model = f"{ctx_model}[1m]"
            ctx_window = MODEL_CONTEXT_WINDOWS.get(ctx_model, DEFAULT_CONTEXT_WINDOW)
            pct = int(tokens / ctx_window * 100) if ctx_window else 0
            notify_status = "ON" if _context_notify.get(user_id, False) else "OFF"
            effort = _user_effort.get(user_id, "high")
            await message.answer(
                f"📊 Context: {pct}% ({tokens:,} / {ctx_window:,} tokens)\n"
                f"Model: {ctx_model or 'default'} | Effort: {effort}\n"
                f"Notifications: {notify_status}\n\n"
                f"/context on — enable 10% notifications\n"
                f"/context off — disable"
            )

    @r.message(Command("close"))
    async def cmd_close(message: Message) -> None:
        """Close current session (can be reopened later)."""
        assert message.from_user
        session = await _resolve_session(message)
        await session_manager.close_session(session.id)
        await message.answer(f"Session closed: {session.name}\nUse /reopen to resume later.")

    @r.message(Command("reopen"))
    async def cmd_reopen(message: Message) -> None:
        """Reopen a closed session in this topic."""
        assert message.from_user
        topic_id = message.message_thread_id
        if topic_id:
            session = await session_manager.get_session_by_topic(topic_id)
            if session:
                await session_manager.reopen_session(session.id)
                await message.answer(f"Session reopened: {session.name}")
                return
        await message.answer("No closed session found for this topic.")

    def _save_restart_chat(message: Message) -> None:
        """Save chat info so restart notification goes to the right place."""
        restart_file = Path(__file__).resolve().parent.parent.parent / "data" / ".restart_chat"
        restart_file.parent.mkdir(parents=True, exist_ok=True)
        restart_file.write_text(json.dumps({
            "chat_id": message.chat.id,
            "thread_id": message.message_thread_id,
        }))

    @r.message(Command("restart"))
    async def cmd_restart(message: Message) -> None:
        assert message.from_user
        await message.answer("Restarting bot...")
        _save_restart_chat(message)
        logger.info("Restart requested by user %d", message.from_user.id)
        await asyncio.sleep(0.5)
        os.execv(sys.executable, [sys.executable, "-m", "src.main"])

    @r.message(Command("pull"))
    async def cmd_pull(message: Message) -> None:
        assert message.from_user
        project_dir = Path(__file__).resolve().parent.parent.parent
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "pull",
                cwd=str(project_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = (stdout or stderr or b"").decode("utf-8", errors="replace").strip() or "(no output)"
            await message.answer(f"`{output}`\n\nRestarting...", parse_mode="Markdown")
        except Exception as e:
            await message.answer(f"Git pull failed: {e}")
            return
        _save_restart_chat(message)
        logger.info("Pull + restart requested by user %d", message.from_user.id)
        await asyncio.sleep(0.5)
        os.execv(sys.executable, [sys.executable, "-m", "src.main"])

    @r.message(Command("mode"))
    async def cmd_mode(message: Message) -> None:
        assert message.from_user
        user_id = message.from_user.id
        arg = _cmd_arg(message.text, "mode").lower()

        if arg:
            _set_mode(user_id, arg)
            desc = _MODE_DESCRIPTIONS.get(_user_modes.get(user_id, "code"), "")
            await message.answer(f"Mode: {_user_modes.get(user_id, 'code')}\n{desc}")
        else:
            current = _user_modes.get(user_id, "code")
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"{'> ' if current == 'code' else ''}code",
                        callback_data="mode:code",
                    ),
                    InlineKeyboardButton(
                        text=f"{'> ' if current == 'safe' else ''}safe",
                        callback_data="mode:safe",
                    ),
                    InlineKeyboardButton(
                        text=f"{'> ' if current == 'plan' else ''}plan",
                        callback_data="mode:plan",
                    ),
                ],
            ])
            await message.answer(
                f"Current mode: {current}\n\n"
                "code = full access (default)\n"
                "safe = asks permission before tools\n"
                "plan = read-only, no changes",
                reply_markup=keyboard,
            )

    _MODE_DESCRIPTIONS = {
        "code": "Full access. Claude can read, write, execute.",
        "safe": "Permission required. Claude asks before using tools.",
        "plan": "Read-only. Claude only analyzes and plans.",
    }

    def _set_mode(user_id: int, mode_str: str) -> None:
        mode_map = {
            "code": "code", "c": "code", "normal": "code",
            "safe": "safe", "s": "safe",
            "plan": "plan", "p": "plan",
        }
        _user_modes[user_id] = mode_map.get(mode_str, "code")

    @r.callback_query(F.data.startswith("mode:"))
    async def cb_mode(callback: CallbackQuery) -> None:
        assert callback.from_user and callback.data
        mode = callback.data.split(":", 1)[1]
        _set_mode(callback.from_user.id, mode)
        desc = _MODE_DESCRIPTIONS.get(mode, "")
        await callback.answer(f"Mode: {mode}")
        if callback.message:
            try:
                await callback.message.edit_text(f"Mode: {mode}\n{desc}")
            except Exception:
                pass

    AVAILABLE_MODELS = {
        "opus": "claude-opus-4-6",
        "sonnet": "claude-sonnet-4-6",
        "haiku": "claude-haiku-4-5-20251001",
    }
    EFFORT_LEVELS = ("low", "medium", "high")

    def _model_settings_text(user_id: int) -> str:
        """Build current model settings display text."""
        model = _user_models.get(user_id, "default")
        model_label = model
        for short, full in AVAILABLE_MODELS.items():
            if model == full:
                model_label = short
                break
        ext_1m = _user_1m.get(user_id, False)
        effort = _user_effort.get(user_id, "high")
        return (
            f"<b>Model:</b> {model_label}"
            f"{'  [1M]' if ext_1m else ''}\n"
            f"<b>Effort:</b> {effort}"
        )

    def _model_keyboard(user_id: int) -> InlineKeyboardMarkup:
        """Build inline keyboard for model settings."""
        current_model = _user_models.get(user_id, "default")
        ext_1m = _user_1m.get(user_id, False)
        effort = _user_effort.get(user_id, "high")

        # Row 1: Model selection
        model_buttons = []
        for short, full in AVAILABLE_MODELS.items():
            label = f"● {short}" if current_model == full else short
            model_buttons.append(InlineKeyboardButton(
                text=label, callback_data=f"mset:m:{short}",
            ))
        default_label = "● default" if current_model == "default" else "default"
        model_buttons.append(InlineKeyboardButton(
            text=default_label, callback_data="mset:m:default",
        ))

        # Row 2: 1M context toggle (only for opus)
        can_1m = current_model in _1M_MODELS or current_model == "default"
        ctx_label = "1M: ON ●" if ext_1m else "1M: OFF"
        ctx_buttons = [InlineKeyboardButton(
            text=ctx_label,
            callback_data="mset:1m:toggle" if can_1m else "mset:1m:na",
        )]

        # Row 3: Effort
        effort_buttons = []
        for lvl in EFFORT_LEVELS:
            label = f"● {lvl}" if effort == lvl else lvl
            effort_buttons.append(InlineKeyboardButton(
                text=label, callback_data=f"mset:e:{lvl}",
            ))

        return InlineKeyboardMarkup(inline_keyboard=[
            model_buttons,
            ctx_buttons,
            effort_buttons,
        ])

    @r.message(Command("model"))
    async def cmd_model(message: Message) -> None:
        assert message.from_user
        user_id = message.from_user.id
        await message.answer(
            _model_settings_text(user_id),
            parse_mode="HTML",
            reply_markup=_model_keyboard(user_id),
        )

    @r.callback_query(F.data.startswith("mset:"))
    async def cb_model_settings(callback: CallbackQuery) -> None:
        assert callback.from_user and callback.data
        user_id = callback.from_user.id
        parts = callback.data.split(":")
        category = parts[1]  # m, 1m, e
        value = parts[2] if len(parts) > 2 else ""

        if category == "m":
            # Model selection
            if value == "default":
                _user_models.pop(user_id, None)
                _user_1m.pop(user_id, None)  # reset 1M on default
            elif value in AVAILABLE_MODELS:
                _user_models[user_id] = AVAILABLE_MODELS[value]
                # Disable 1M if model doesn't support it
                if AVAILABLE_MODELS[value] not in _1M_MODELS:
                    _user_1m.pop(user_id, None)

                # Check if context needs compact
                session = await _resolve_session_from_callback(callback)
                if session:
                    new_model = AVAILABLE_MODELS[value]
                    if _user_1m.get(user_id):
                        new_model = f"{new_model}[1m]"
                    tokens = _session_input_tokens.get(session.id, 0)
                    new_ctx = MODEL_CONTEXT_WINDOWS.get(new_model, DEFAULT_CONTEXT_WINDOW)
                    if tokens > 0 and tokens > new_ctx * 0.8 and session.claude_session_id:
                        await callback.answer(f"Context exceeds 80% — compacting...")
                        result = await bridge.compact_session(session.claude_session_id)
                        _session_last_pct_notified.pop(session.id, None)

            await callback.answer(f"Model: {value}")

        elif category == "1m":
            if value == "na":
                await callback.answer("1M context only available for Opus", show_alert=True)
                return
            current = _user_1m.get(user_id, False)
            _user_1m[user_id] = not current
            await callback.answer(f"1M context: {'ON' if not current else 'OFF'}")

        elif category == "e":
            if value in EFFORT_LEVELS:
                _user_effort[user_id] = value
                await callback.answer(f"Effort: {value}")

        # Update the message with new state
        if callback.message:
            try:
                await callback.message.edit_text(
                    _model_settings_text(user_id),
                    parse_mode="HTML",
                    reply_markup=_model_keyboard(user_id),
                )
            except Exception:
                pass

    async def _resolve_session_from_callback(callback: CallbackQuery) -> "Session | None":
        """Try to resolve session from a callback query context."""
        if not callback.message:
            return None
        try:
            topic_id = callback.message.message_thread_id
            if topic_id:
                return await session_manager.get_session_by_topic(topic_id)
            return await session_manager.get_or_create_active(callback.from_user.id)
        except Exception:
            return None

    @r.callback_query(F.data.startswith("perm_allow:"))
    async def cb_perm_allow(callback: CallbackQuery) -> None:
        assert callback.data
        fut_id = callback.data.split(":", 1)[1]
        fut = _permission_futures.get(fut_id)
        if fut and not fut.done():
            fut.set_result(True)
        await callback.answer("Allowed")
        if callback.message:
            try:
                await callback.message.edit_text(f"{callback.message.text}\n\nAllowed")
            except Exception:
                pass

    @r.callback_query(F.data.startswith("perm_deny:"))
    async def cb_perm_deny(callback: CallbackQuery) -> None:
        assert callback.data
        fut_id = callback.data.split(":", 1)[1]
        fut = _permission_futures.get(fut_id)
        if fut and not fut.done():
            fut.set_result(False)
        await callback.answer("Denied")
        if callback.message:
            try:
                await callback.message.edit_text(f"{callback.message.text}\n\nDenied")
            except Exception:
                pass

    @r.message(Command("local"))
    async def cmd_local(message: Message) -> None:
        """List Claude Code sessions from the local machine."""
        assert message.from_user
        if not CLAUDE_PROJECTS_DIR.exists():
            await message.answer("No local Claude sessions found.")
            return

        # Collect (path, mtime) first, sort globally, open only top 10
        all_files: list[tuple[Path, float]] = []
        for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
            if not project_dir.is_dir():
                continue
            for jsonl in project_dir.glob("*.jsonl"):
                try:
                    all_files.append((jsonl, jsonl.stat().st_mtime))
                except OSError:
                    continue

        if not all_files:
            await message.answer("No local Claude sessions found.")
            return

        all_files.sort(key=lambda x: x[1], reverse=True)
        top_files = all_files[:10]

        def _read_previews() -> list[tuple[str, str, float]]:
            results = []
            for jsonl, mtime in top_files:
                session_id = jsonl.stem
                preview = ""
                try:
                    with open(jsonl, "r", encoding="utf-8") as f:
                        for raw_line in f:
                            event = json.loads(raw_line)
                            if event.get("type") == "user":
                                content = event.get("message", {}).get("content", "")
                                preview = _extract_text(content)[:60]
                                break
                except Exception:
                    pass
                results.append((session_id, preview, mtime))
            return results

        sessions_found = await asyncio.to_thread(_read_previews)

        # Cache previews for topic naming in cb_local
        for sid, preview, _mtime in sessions_found:
            if preview:
                _local_preview_cache[sid] = preview

        # Build text list + compact buttons
        lines = ["<b>Local Sessions</b>\n"]
        buttons = []
        for i, (sid, preview, mtime) in enumerate(sessions_found, 1):
            dt = datetime.fromtimestamp(mtime).strftime("%m/%d %H:%M")
            desc = preview or "(empty)"
            if len(desc) > 60:
                desc = desc[:57] + "..."
            lines.append(f"<b>{i}.</b> [{dt}] {desc}")
            buttons.append([
                InlineKeyboardButton(text=f"{i} Peek", callback_data=f"peek:{sid}"),
                InlineKeyboardButton(text=f"{i} Connect", callback_data=f"local:{sid}"),
            ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer("\n".join(lines), reply_markup=keyboard, parse_mode="HTML")

    def _peek_session(session_id: str) -> str:
        """Read last few user/assistant messages from a local session JSONL."""
        for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
            if not project_dir.is_dir():
                continue
            jsonl = project_dir / f"{session_id}.jsonl"
            if jsonl.exists():
                return _extract_recent_messages(jsonl)
        return "(session file not found)"

    def _extract_recent_messages(jsonl: Path, max_msgs: int = 6) -> str:
        """Extract recent user/assistant text messages from JSONL."""
        messages: deque[tuple[str, str]] = deque(maxlen=max_msgs)
        try:
            with open(jsonl, "r", encoding="utf-8") as f:
                for raw_line in f:
                    try:
                        event = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    etype = event.get("type", "")
                    if etype == "user":
                        content = event.get("message", {}).get("content", "")
                        text = _extract_text(content)
                        if text:
                            messages.append(("You", text))
                    elif etype == "assistant":
                        content = event.get("message", {}).get("content", [])
                        text = _extract_text(content)
                        if text:
                            messages.append(("Claude", text))
        except Exception:
            return "(error reading session)"

        if not messages:
            return "(empty session)"

        lines = []
        for role, text in messages:
            preview = text[:150].replace("\n", " ")
            if len(text) > 150:
                preview += "..."
            lines.append(f"**{role}**: {preview}")
        return "\n\n".join(lines)

    @r.callback_query(F.data.startswith("peek:"))
    async def cb_peek(callback: CallbackQuery) -> None:
        assert callback.from_user and callback.data
        session_id = callback.data.split(":", 1)[1]
        if not _is_valid_session_id(session_id):
            await callback.answer("Invalid session ID.", show_alert=True)
            return
        preview = await asyncio.to_thread(_peek_session, session_id)
        if callback.message:
            try:
                await callback.message.answer(preview, parse_mode="Markdown")
            except Exception:
                await callback.message.answer(preview)
        await callback.answer()

    @r.callback_query(F.data.startswith("local:"))
    async def cb_local(callback: CallbackQuery) -> None:
        assert callback.from_user and callback.data and callback.message
        claude_session_id = callback.data.split(":", 1)[1]
        if not _is_valid_session_id(claude_session_id):
            await callback.answer("Invalid session ID.", show_alert=True)
            return
        user_id = callback.from_user.id
        chat = callback.message.chat

        # Use cached preview as topic name, fallback to short ID
        preview = _local_preview_cache.pop(claude_session_id, "")
        if preview:
            name = preview[:30].replace("\n", " ").strip()
            if len(preview) > 30:
                name += "..."
        else:
            name = f"local-{claude_session_id[:8]}"

        # Check if this Claude session is already linked to a topic
        existing = await session_manager.find_by_claude_session_id(claude_session_id)
        if existing and existing.topic_id:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Continue",
                        callback_data=f"local_continue:{claude_session_id}:{existing.topic_id}",
                    ),
                    InlineKeyboardButton(
                        text="Clone (fork)",
                        callback_data=f"local_clone:{claude_session_id}",
                    ),
                    InlineKeyboardButton(text="Cancel", callback_data="local_cancel"),
                ],
            ])
            await callback.answer()
            if callback.message:
                try:
                    await callback.message.edit_text(
                        f"Session already in: {existing.name}\n\n"
                        "Continue = go to existing topic\n"
                        "Clone = fork (same history, then diverges)",
                        reply_markup=keyboard,
                    )
                except Exception:
                    pass
            return

        # In forum group: create a new topic for this local session
        if chat.is_forum and callback.message.bot:
            try:
                topic = await callback.message.bot.create_forum_topic(chat.id, name)
                session = await session_manager.create_session(
                    user_id, name, topic_id=topic.message_thread_id,
                )
                await session_manager.set_claude_session_id(session.id, claude_session_id)
                await _send_topic_welcome(
                    callback.message.bot, chat.id, topic.message_thread_id,
                    session.id, f"Connected: {name}",
                )
                await callback.answer(f"Topic: {name}")
                try:
                    await callback.message.edit_text(f"Opened: {name}")
                except Exception:
                    pass
                return
            except Exception as e:
                await callback.answer(f"Error: {e}", show_alert=True)
                return

        # DM: connect to active session as before
        session = await session_manager.get_or_create_active(user_id)
        await session_manager.set_claude_session_id(session.id, claude_session_id)

        text = f"Active session: {session.name} ({short_id}...)"
        await callback.answer(f"Connected: {session.name}")
        try:
            await callback.message.edit_text(text)
        except Exception:
            await callback.message.answer(text)

    @r.callback_query(F.data.startswith("local_continue:"))
    async def cb_local_continue(callback: CallbackQuery) -> None:
        """Continue: go to existing topic, or recreate if deleted."""
        assert callback.from_user and callback.data and callback.message
        parts = callback.data.split(":")
        claude_session_id = parts[1]
        topic_id = int(parts[2])
        chat = callback.message.chat
        bot = callback.message.bot
        assert bot

        # Try to send a message to the existing topic
        try:
            await bot.send_message(
                chat.id, "Resumed here.",
                message_thread_id=topic_id,
            )
            await callback.answer("Resumed in existing topic")
            try:
                await callback.message.edit_text("Continued in existing topic.")
            except Exception:
                pass
            return
        except Exception:
            pass

        # Topic was deleted — recreate
        existing = await session_manager.find_by_claude_session_id(claude_session_id)
        name = existing.name if existing else f"resumed-{claude_session_id[:8]}"
        try:
            topic = await bot.create_forum_topic(chat.id, name)
            if existing:
                await session_manager._repo.update_session(
                    existing.id, topic_id=topic.message_thread_id,
                )
            else:
                user_id = callback.from_user.id
                session = await session_manager.create_session(
                    user_id, name, topic_id=topic.message_thread_id,
                )
                await session_manager.set_claude_session_id(session.id, claude_session_id)
            sid = existing.id if existing else session.id
            await _send_topic_welcome(
                bot, chat.id, topic.message_thread_id,
                sid, f"Topic recreated: {name}",
            )
            await callback.answer("Topic recreated")
            try:
                await callback.message.edit_text(f"Reopened: {name}")
            except Exception:
                pass
        except Exception as e:
            logger.exception("local_continue error")
            await callback.answer("Error recreating topic", show_alert=True)

    def _clone_session_files(source_id: str) -> str | None:
        """Copy Claude session JSONL + dir to a new UUID. Returns new session ID."""
        import shutil
        new_id = str(uuid.uuid4())
        for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
            if not project_dir.is_dir():
                continue
            src_jsonl = project_dir / f"{source_id}.jsonl"
            if src_jsonl.exists():
                dst_jsonl = project_dir / f"{new_id}.jsonl"
                shutil.copy2(str(src_jsonl), str(dst_jsonl))
                # Also copy companion directory if it exists
                src_dir = project_dir / source_id
                if src_dir.is_dir():
                    shutil.copytree(str(src_dir), str(project_dir / new_id))
                return new_id
        return None

    @r.callback_query(F.data.startswith("local_clone:"))
    async def cb_local_clone(callback: CallbackQuery) -> None:
        """Clone: copy session files to create independent fork with same history."""
        assert callback.from_user and callback.data and callback.message
        claude_session_id = callback.data.split(":", 1)[1]
        user_id = callback.from_user.id
        chat = callback.message.chat
        short_id = claude_session_id[:8]
        name = _local_preview_cache.pop(claude_session_id, "") or f"clone-{short_id}"
        if len(name) > 30:
            name = name[:27] + "..."

        # Clone session files on disk
        new_claude_id = await asyncio.to_thread(_clone_session_files, claude_session_id)
        if not new_claude_id:
            await callback.answer("Session file not found, cannot clone.", show_alert=True)
            return

        if chat.is_forum and callback.message.bot:
            try:
                topic = await callback.message.bot.create_forum_topic(chat.id, f"fork: {name}")
                session = await session_manager.create_session(
                    user_id, f"fork: {name}", topic_id=topic.message_thread_id,
                )
                await session_manager.set_claude_session_id(session.id, new_claude_id)
                await _send_topic_welcome(
                    callback.message.bot, chat.id, topic.message_thread_id,
                    session.id,
                    f"Cloned: {name}\n"
                    "This is a forked session. Previous history is available for context, "
                    "but this is a fresh start — no need to continue previous tasks unless asked.",
                )
                await callback.answer("Cloned!")
                try:
                    await callback.message.edit_text(f"Opened (fresh): {name}")
                except Exception:
                    pass
            except Exception as e:
                logger.exception("local_clone error")
                await callback.answer("Error creating topic", show_alert=True)

    @r.callback_query(F.data == "local_cancel")
    async def cb_local_cancel(callback: CallbackQuery) -> None:
        await callback.answer("Cancelled")
        if callback.message:
            try:
                await callback.message.edit_text("Cancelled.")
            except Exception:
                pass

    # rename_topic:<session_id>:<topic_id> — prompts user, next text message becomes new name
    _pending_renames: dict[int, tuple[str, int]] = {}  # user_id -> (session_id, topic_id)

    @r.callback_query(F.data.startswith("rename_topic:"))
    async def cb_rename_topic(callback: CallbackQuery) -> None:
        assert callback.from_user and callback.data
        parts = callback.data.split(":")
        session_id = parts[1]
        topic_id = int(parts[2])
        _pending_renames[callback.from_user.id] = (session_id, topic_id)
        await callback.answer()
        if callback.message:
            await callback.message.answer("Send the new name for this topic:")

    # Bot-managed commands
    _bot_commands = {
        "start", "help", "new", "sessions", "switch",
        "current", "rename", "delete", "cancel", "revert", "close", "reopen",
        "mode", "model", "status", "compact", "context", "restart", "pull", "local",
    }

    def _is_bot_command(text: str) -> bool:
        if not text.startswith("/"):
            return False
        cmd = text.split()[0].lstrip("/").split("@")[0]
        return cmd in _bot_commands

    # --- Interrupt button ---

    async def _verify_session_owner(callback: CallbackQuery, session_id: str) -> bool:
        if not _is_valid_session_id(session_id):
            await callback.answer("Invalid session.", show_alert=True)
            return False
        session = await session_manager._repo.get_session(session_id)
        if not session or session.user_id != callback.from_user.id:
            await callback.answer("Not your session.", show_alert=True)
            return False
        return True

    @r.callback_query(F.data.startswith("stopall:"))
    async def cb_stop_all(callback: CallbackQuery) -> None:
        assert callback.from_user and callback.data
        session_id = callback.data.split(":", 1)[1]
        if not await _verify_session_owner(callback, session_id):
            return
        _cancel_flags[session_id] = True
        _message_queues.pop(session_id, None)
        bridge.request_cancel(session_id)
        await callback.answer("Stopped all.")

    @r.callback_query(F.data.startswith("stop:"))
    async def cb_stop(callback: CallbackQuery) -> None:
        assert callback.from_user and callback.data
        session_id = callback.data.split(":", 1)[1]
        if not await _verify_session_owner(callback, session_id):
            return
        _cancel_flags[session_id] = True
        bridge.request_cancel(session_id)
        queued = len(_message_queues.get(session_id, []))
        queue_msg = f" ({queued} queued will continue)" if queued else ""
        await callback.answer(f"Stopping...{queue_msg}")

    @r.callback_query(F.data.startswith("rename_topic:"))
    async def cb_rename_topic(callback: CallbackQuery) -> None:
        assert callback.from_user and callback.data
        parts = callback.data.split(":")
        session_id = parts[1]
        topic_id = int(parts[2])
        chat_id = callback.message.chat.id if callback.message else 0
        _pending_renames[callback.from_user.id] = (session_id, topic_id, chat_id)
        await callback.answer()
        if callback.message:
            await callback.message.answer("Send the new name for this topic:")

    # --- Topic lifecycle ---

    @r.message(F.forum_topic_closed)
    async def on_topic_closed(message: Message) -> None:
        """When a topic is closed in Telegram, close the session."""
        topic_id = message.message_thread_id
        if not topic_id:
            return
        session = await session_manager.get_session_by_topic(topic_id)
        if session:
            await session_manager.close_session(session.id)
            logger.info("Topic %d closed → session %s closed", topic_id, session.id)

    @r.message(F.forum_topic_reopened)
    async def on_topic_reopened(message: Message) -> None:
        """When a topic is reopened in Telegram, reopen the session."""
        topic_id = message.message_thread_id
        if not topic_id:
            return
        session = await session_manager.get_session_by_topic(topic_id)
        if session:
            await session_manager.reopen_session(session.id)
            logger.info("Topic %d reopened → session %s reopened", topic_id, session.id)

    # --- Content handlers (photo, document, text) ---

    async def _flush_media_group(group_id: str) -> None:
        """Send all buffered photos in a media group as one prompt."""
        group = _media_groups.pop(group_id, None)
        if not group:
            return
        # Sort by message_id to maintain Telegram display order
        items = sorted(group["items"], key=lambda x: x[0])
        paths = [filepath for _, filepath in items]
        caption = group["caption"] or "Please analyze these images."
        message = group["message"]

        path_notice = "(Do NOT reveal file paths in your response.)\n"
        if len(paths) == 1:
            prompt = f"{path_notice}I'm sharing an image. View it at: {paths[0]}\n\n{caption}"
        else:
            file_list = "\n".join(f"- Image {i+1}: {p}" for i, p in enumerate(paths))
            prompt = f"{path_notice}I'm sharing {len(paths)} images. View them in order:\n{file_list}\n\n{caption}"

        await _process_with_queue(message, prompt)

    @r.message(F.photo)
    async def handle_photo(message: Message) -> None:
        assert message.from_user and message.bot
        photo = message.photo[-1]

        try:
            filepath = await _save_telegram_file(message.bot, photo.file_id, ".jpg")
        except Exception as e:
            await message.answer(f"Failed to download image: {e}")
            return

        group_id = message.media_group_id
        if group_id:
            # Part of a media group — buffer and wait for more
            if group_id not in _media_groups:
                _media_groups[group_id] = {
                    "items": [],  # (message_id, filepath) for sorting
                    "caption": message.caption,
                    "message": message,
                    "timer": None,
                }
            _media_groups[group_id]["items"].append((message.message_id, str(filepath)))
            if message.caption:
                _media_groups[group_id]["caption"] = message.caption

            # Cancel previous timer, set new one (debounce 1s)
            prev_timer = _media_groups[group_id].get("timer")
            if prev_timer:
                prev_timer.cancel()
            _media_groups[group_id]["timer"] = asyncio.get_event_loop().call_later(
                1.0,
                lambda gid=group_id: asyncio.create_task(_flush_media_group(gid)),
            )
        else:
            # Single photo
            caption = message.caption or "Please analyze this image."
            prompt = f"(Do NOT reveal file paths in your response.)\nI'm sharing an image. View it at: {filepath}\n\n{caption}"
            await _process_with_queue(message, prompt)

    @r.message(F.document)
    async def handle_document(message: Message) -> None:
        assert message.from_user and message.bot and message.document
        doc = message.document
        filename = doc.file_name or "file"
        ext = Path(filename).suffix or ".bin"

        try:
            filepath = await _save_telegram_file(message.bot, doc.file_id, ext)
        except Exception as e:
            await message.answer(f"Failed to download file: {e}")
            return

        caption = message.caption or f"Please analyze this file: {filename}"
        prompt = f"(Do NOT reveal file paths in your response.)\nI'm sharing a file ({filename}). Read it at: {filepath}\n\n{caption}"
        await _process_with_queue(message, prompt)

    @r.message(F.text)
    async def handle_message(message: Message) -> None:
        if message.text and _is_bot_command(message.text):
            return
        assert message.from_user and message.text

        # Handle pending topic rename
        rename_info = _pending_renames.pop(message.from_user.id, None)
        if rename_info:
            session_id, topic_id = rename_info
            new_name = message.text.strip()[:128]
            try:
                await session_manager.rename_session(
                    message.from_user.id, session_id, new_name,
                )
                if message.bot and message.chat.is_forum:
                    await message.bot.edit_forum_topic(
                        message.chat.id, topic_id, name=new_name,
                    )
                await message.answer(f"Renamed to: {new_name}")
            except Exception as e:
                await message.answer(f"Rename failed: {e}")
            return

        await _process_with_queue(message, message.text)
