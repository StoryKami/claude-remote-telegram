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
# Cache: claude_session_id → preview text (for naming topics from /local)
_local_preview_cache: dict[str, str] = {}

PLAN_MODE_PREFIX = (
    "[PLAN MODE] You are in plan mode. Do NOT edit, write, or create any files. "
    "Do NOT execute commands that modify state. Only analyze, research, and provide plans.\n\n"
)

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

_SESSION_ID_RE = re.compile(r"^[a-f0-9\-]{8,36}$")


def _is_valid_session_id(sid: str) -> bool:
    return bool(_SESSION_ID_RE.match(sid))


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
        """Tracks and renders the status message with 1s spinner refresh."""

        FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

        def __init__(self, status_msg: Message, session_id: str, start: float) -> None:
            self.msg = status_msg
            self.session_id = session_id
            self.start = start
            self.phase = "Thinking..."
            self.hint = ""
            self.last_text = ""
            self.current_tool = ""
            self.steps: list[tuple[str, int]] = []
            self._frame_idx = 0
            self._last_rendered = ""
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

            header = f"{spinner} {self.phase} ({e}s)"
            parts = [header]

            if self.hint and not self.steps and not self.last_text:
                parts.append(f"\n&gt; {self.hint}")

            if self.last_text:
                preview = self.last_text[-200:].replace("<", "&lt;").replace(">", "&gt;")
                parts.append(f"\n💬 {preview}")

            if self.steps or self.current_tool:
                log_lines = [f"✓ {name} ({t}s)" for name, t in self.steps[-6:]]
                if self.current_tool:
                    log_lines.append(f"⏳ {self.current_tool}")
                parts.append(f'\n<blockquote expandable>{chr(10).join(log_lines)}</blockquote>')

            return "\n".join(parts)

        async def refresh(self) -> None:
            html = self.render()
            if html == self._last_rendered:
                return
            self._last_rendered = html
            try:
                await self.msg.edit_text(html, parse_mode="HTML", reply_markup=self._stop_kb)
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except Exception:
                pass

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
        hint = display_prompt[:80].replace("\n", " ").replace("<", "&lt;").replace(">", "&gt;")
        if len(display_prompt) > 80:
            hint += "..."

        start_time = time.monotonic()
        status_msg = await message.answer("⠋ Thinking... (0s)", parse_mode="HTML")

        tracker = _StatusTracker(status_msg, session.id, start_time)
        tracker.hint = hint
        if mode != "code":
            tracker.phase = f"[{mode}] Thinking..."

        ticker_task = asyncio.create_task(_run_status_ticker(tracker))
        accumulated_text = ""
        accumulated_thinking = ""

        try:
            async for event in bridge.send_message(
                prompt=prompt,
                claude_session_id=session.claude_session_id,
            ):
                if _cancel_flags.get(session.id):
                    raise asyncio.CancelledError()

                if event.type == "thinking":
                    accumulated_thinking += event.data

                elif event.type == "text":
                    accumulated_text += event.data
                    tracker.phase = "Writing..."
                    # Show latest text snippet as Claude's utterance
                    tracker.last_text = accumulated_text[-200:]

                elif event.type == "tool_use":
                    tracker.phase = "Working..."
                    tracker.current_tool = event.data

                elif event.type == "tool_result":
                    tracker.steps.append((tracker.current_tool or "?", tracker.elapsed()))
                    tracker.current_tool = ""

                elif event.type == "error":
                    ticker_task.cancel()
                    logger.error("CLI error: %s", event.data[:500])
                    await status_msg.edit_text("Error: Claude CLI encountered an error. Check logs for details.")
                    return

                elif event.type == "done":
                    if event.session_id:
                        await session_manager.set_claude_session_id(
                            session.id, event.session_id
                        )
                    await session_manager.touch_session(session.id)
                    if event.data and not accumulated_text:
                        accumulated_text = event.data

        except asyncio.CancelledError:
            await status_msg.edit_text("Cancelled.")
            return
        except Exception as e:
            logger.exception("Error processing message")
            await status_msg.edit_text("Error: something went wrong. Check logs for details.")
            return
        finally:
            ticker_task.cancel()

        if not accumulated_text:
            await status_msg.edit_text("(no response)")
            return

        # Build process log as expandable summary above the response
        total_elapsed = tracker.elapsed()
        log_lines: list[str] = []
        if accumulated_thinking:
            thinking_preview = accumulated_thinking[-300:].replace("\n", " ").strip()
            if len(accumulated_thinking) > 300:
                thinking_preview = "..." + thinking_preview
            log_lines.append(f"💭 {thinking_preview}")
            log_lines.append("")
        for step_name, step_time in tracker.steps:
            log_lines.append(f"✓ {step_name} ({step_time}s)")
        log_lines.append(f"\n⏱ {total_elapsed}s")

        if log_lines:
            log_html = "\n".join(log_lines)
            try:
                await status_msg.edit_text(
                    f'<blockquote expandable>{log_html}</blockquote>',
                    parse_mode="HTML",
                    reply_markup=None,
                )
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
                try:
                    await status_msg.edit_text(
                        f'<blockquote expandable>{log_html}</blockquote>',
                        parse_mode="HTML",
                        reply_markup=None,
                    )
                except Exception:
                    pass
            except Exception:
                pass  # keep the message, don't delete

        chunks = format_telegram_message(accumulated_text)
        for chunk in chunks:
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
            while _message_queues.get(session.id):
                queued_msg, queued_prompt = _message_queues[session.id].popleft()
                _cancel_flags[session.id] = False
                # Re-resolve in case session was updated
                session = await _resolve_session(queued_msg)
                await _process_prompt(queued_msg, session, queued_prompt)

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
        name = (message.text or "").replace("/new", "").strip() or "untitled"
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
        session_id = (message.text or "").replace("/switch", "").strip()
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
        name = (message.text or "").replace("/rename", "").strip()
        if not name:
            await message.answer("Usage: /rename <new name>")
            return
        session = await session_manager.get_or_create_active(message.from_user.id)
        await session_manager.rename_session(message.from_user.id, session.id, name)
        await message.answer(f"Renamed to: **{name}**", parse_mode="Markdown")

    @r.message(Command("delete"))
    async def cmd_delete(message: Message) -> None:
        assert message.from_user
        session_id = (message.text or "").replace("/delete", "").strip()
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
        await message.answer("Cancelling... (queue cleared)")

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
        arg = (message.text or "").replace("/mode", "").strip().lower()

        if arg in ("plan", "p"):
            _user_modes[user_id] = "plan"
            await message.answer(
                "Switched to **plan** mode. Claude will only analyze and plan, not modify files.",
                parse_mode="Markdown",
            )
        elif arg in ("code", "c", "normal"):
            _user_modes[user_id] = "code"
            await message.answer(
                "Switched to **code** mode. Claude can read, write, and execute.",
                parse_mode="Markdown",
            )
        else:
            current = _user_modes.get(user_id, "code")
            await message.answer(
                f"Current mode: **{current}**\n\n"
                "`/mode plan` — plan only (no file changes)\n"
                "`/mode code` — full access (default)",
                parse_mode="Markdown",
            )

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
                    session.id, f"Cloned: {name}\nSame history, independent from now.",
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
        "current", "rename", "delete", "cancel", "close", "reopen",
        "mode", "restart", "pull", "local",
    }

    def _is_bot_command(text: str) -> bool:
        if not text.startswith("/"):
            return False
        cmd = text.split()[0].lstrip("/").split("@")[0]
        return cmd in _bot_commands

    # --- Interrupt button ---

    @r.callback_query(F.data.startswith("stop:"))
    async def cb_stop(callback: CallbackQuery) -> None:
        assert callback.from_user and callback.data
        session_id = callback.data.split(":", 1)[1]
        if not _is_valid_session_id(session_id):
            await callback.answer("Invalid session.", show_alert=True)
            return
        # Verify ownership
        session = await session_manager._repo.get_session(session_id)
        if not session or session.user_id != callback.from_user.id:
            await callback.answer("Not your session.", show_alert=True)
            return
        _cancel_flags[session_id] = True
        _message_queues.pop(session_id, None)
        await callback.answer("Stopping...")

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

    @r.message(F.photo)
    async def handle_photo(message: Message) -> None:
        assert message.from_user and message.bot
        photo = message.photo[-1]

        try:
            filepath = await _save_telegram_file(message.bot, photo.file_id, ".jpg")
        except Exception as e:
            await message.answer(f"Failed to download image: {e}")
            return

        caption = message.caption or "Please analyze this image."
        prompt = f"I'm sharing an image. View it at: {filepath}\n\n{caption}"
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
        prompt = f"I'm sharing a file ({filename}). Read it at: {filepath}\n\n{caption}"
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
