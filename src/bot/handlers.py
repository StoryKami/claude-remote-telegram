from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from aiogram import Bot, Router, F
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

# Per-user state
_user_locks: dict[int, asyncio.Lock] = {}
_cancel_flags: dict[int, bool] = {}
_user_modes: dict[int, str] = {}
_message_queues: dict[int, list[tuple[Message, str]]] = {}

PLAN_MODE_PREFIX = (
    "[PLAN MODE] You are in plan mode. Do NOT edit, write, or create any files. "
    "Do NOT execute commands that modify state. Only analyze, research, and provide plans.\n\n"
)


def _get_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


def setup_handlers(
    r: Router,
    bridge: ClaudeBridge,
    session_manager: SessionManager,
    workspace_path: Path,
) -> None:
    """Register all handlers with dependencies injected."""

    tmp_dir = workspace_path / "_tmp" / "telegram"

    async def _save_telegram_file(bot: Bot, file_id: str, ext: str = ".jpg") -> Path:
        """Download a Telegram file to workspace tmp dir."""
        tmp_dir.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex[:8]}{ext}"
        dest = tmp_dir / name
        file_info = await bot.get_file(file_id)
        assert file_info.file_path
        await bot.download_file(file_info.file_path, str(dest))
        return dest

    async def _animate_thinking(
        status_msg: Message, mode_label: str, start: float,
    ) -> None:
        """Animate thinking spinner so user knows the bot is alive."""
        frames = ["◐", "◓", "◑", "◒"]
        i = 0
        while True:
            await asyncio.sleep(2)
            elapsed = int(time.monotonic() - start)
            try:
                await status_msg.edit_text(
                    f"{mode_label}{frames[i % len(frames)]} Thinking... ({elapsed}s)"
                )
            except Exception:
                pass
            i += 1

    async def _process_prompt(
        message: Message,
        user_id: int,
        prompt: str,
    ) -> None:
        """Process a single prompt through Claude bridge."""
        _cancel_flags[user_id] = False
        session = await session_manager.get_or_create_active(user_id)
        mode = _user_modes.get(user_id, "code")

        if mode == "plan":
            prompt = PLAN_MODE_PREFIX + prompt

        mode_label = f"[{mode}] " if mode != "code" else ""
        start_time = time.monotonic()
        status_msg = await message.answer(f"{mode_label}Thinking.")
        last_edit = start_time
        accumulated_text = ""
        last_tool_status = ""

        # Start dot animation — cancelled on first real event
        thinking_task = asyncio.create_task(
            _animate_thinking(status_msg, mode_label, start_time)
        )

        try:
            async for event in bridge.send_message(
                prompt=prompt,
                claude_session_id=session.claude_session_id,
            ):
                if _cancel_flags.get(user_id):
                    raise asyncio.CancelledError()

                # Stop animation on first real event
                if not thinking_task.done():
                    thinking_task.cancel()

                if event.type == "text":
                    accumulated_text += event.data
                    now = time.monotonic()
                    if now - last_edit >= 2.5:
                        elapsed = int(now - start_time)
                        preview = accumulated_text[-3500:]
                        if len(accumulated_text) > 3500:
                            preview = "...\n" + preview
                        try:
                            await status_msg.edit_text(
                                f"{preview or '...'}\n\n({elapsed}s)"
                            )
                        except Exception:
                            pass
                        last_edit = now

                elif event.type == "tool_use":
                    last_tool_status = event.data
                    elapsed = int(time.monotonic() - start_time)
                    try:
                        await status_msg.edit_text(
                            f"⏳ {event.data} ({elapsed}s)"
                        )
                    except Exception:
                        pass

                elif event.type == "tool_result":
                    elapsed = int(time.monotonic() - start_time)
                    try:
                        await status_msg.edit_text(
                            f"✓ {last_tool_status} ({elapsed}s)"
                        )
                    except Exception:
                        pass

                elif event.type == "error":
                    await status_msg.edit_text(f"Error: {event.data[:4000]}")
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
            await status_msg.edit_text(f"Error: {e}")
            return
        finally:
            if not thinking_task.done():
                thinking_task.cancel()

        if not accumulated_text:
            await status_msg.edit_text("(no response)")
            return

        chunks = format_telegram_message(accumulated_text)

        try:
            await status_msg.edit_text(chunks[0])
        except Exception:
            await message.answer(chunks[0])

        for chunk in chunks[1:]:
            await message.answer(chunk)

    async def _process_with_queue(
        message: Message, user_id: int, prompt: str,
    ) -> None:
        """Process prompt with per-user lock and message queue."""
        lock = _get_lock(user_id)
        if lock.locked():
            queue = _message_queues.setdefault(user_id, [])
            queue.append((message, prompt))
            await message.answer(f"Queued (#{len(queue)}). Will process after current task.")
            return

        async with lock:
            await _process_prompt(message, user_id, prompt)
            # Drain queued messages
            while _message_queues.get(user_id):
                queued_msg, queued_prompt = _message_queues[user_id].pop(0)
                _cancel_flags[user_id] = False
                await _process_prompt(queued_msg, user_id, queued_prompt)

    # --- Bot commands ---

    @r.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        await message.answer(WELCOME_TEXT, parse_mode="Markdown")

    @r.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        await message.answer(HELP_TEXT, parse_mode="Markdown")

    @r.message(Command("new"))
    async def cmd_new(message: Message) -> None:
        assert message.from_user
        name = (message.text or "").replace("/new", "").strip() or "untitled"
        try:
            session = await session_manager.create_session(message.from_user.id, name)
            await message.answer(
                f"Created session: **{session.name}** (`{session.id}`)\nNew Claude session (no history).",
                parse_mode="Markdown",
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
            await callback.answer(f"Switched to: {session.name}")
            if callback.message:
                await callback.message.edit_text(
                    f"Active session: **{session.name}** (`{session.id}`)",
                    parse_mode="Markdown",
                )
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
            await message.answer(f"Deleted session: `{session_id}`", parse_mode="Markdown")
        except ValueError as e:
            await message.answer(str(e))

    @r.message(Command("cancel"))
    async def cmd_cancel(message: Message) -> None:
        assert message.from_user
        user_id = message.from_user.id
        _cancel_flags[user_id] = True
        _message_queues.pop(user_id, None)
        await message.answer("Cancelling... (queue cleared)")

    @r.message(Command("restart"))
    async def cmd_restart(message: Message) -> None:
        assert message.from_user
        await message.answer("Restarting bot...")
        logger.info("Restart requested by user %d", message.from_user.id)
        # Give Telegram time to deliver the message
        await asyncio.sleep(0.5)
        os.execv(sys.executable, [sys.executable, "-m", "src.main"])

    @r.message(Command("pull"))
    async def cmd_pull(message: Message) -> None:
        assert message.from_user
        project_dir = Path(__file__).resolve().parent.parent.parent
        try:
            result = subprocess.run(
                ["git", "pull"],
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = result.stdout.strip() or result.stderr.strip() or "(no output)"
            await message.answer(f"`{output}`\n\nRestarting...", parse_mode="Markdown")
        except Exception as e:
            await message.answer(f"Git pull failed: {e}")
            return
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

    # Bot-managed commands
    _bot_commands = {
        "start", "help", "new", "sessions", "switch",
        "current", "rename", "delete", "cancel", "mode",
        "restart", "pull",
    }

    def _is_bot_command(text: str) -> bool:
        if not text.startswith("/"):
            return False
        cmd = text.split()[0].lstrip("/").split("@")[0]
        return cmd in _bot_commands

    # --- Content handlers (photo, document, text) ---

    @r.message(F.photo)
    async def handle_photo(message: Message) -> None:
        assert message.from_user and message.bot
        user_id = message.from_user.id
        photo = message.photo[-1]  # largest size

        try:
            filepath = await _save_telegram_file(message.bot, photo.file_id, ".jpg")
        except Exception as e:
            await message.answer(f"Failed to download image: {e}")
            return

        caption = message.caption or "Please analyze this image."
        prompt = f"I'm sharing an image. View it at: {filepath}\n\n{caption}"
        await _process_with_queue(message, user_id, prompt)

    @r.message(F.document)
    async def handle_document(message: Message) -> None:
        assert message.from_user and message.bot and message.document
        user_id = message.from_user.id
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
        await _process_with_queue(message, user_id, prompt)

    @r.message(F.text)
    async def handle_message(message: Message) -> None:
        if message.text and _is_bot_command(message.text):
            return
        assert message.from_user and message.text
        await _process_with_queue(message, message.from_user.id, message.text)
