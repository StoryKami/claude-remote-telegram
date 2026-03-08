from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from aiogram import Router, F
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

# Per-user locks to serialize message processing
_user_locks: dict[int, asyncio.Lock] = {}
# Per-user cancel flags
_cancel_flags: dict[int, bool] = {}


def _get_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


def setup_handlers(
    r: Router,
    bridge: ClaudeBridge,
    session_manager: SessionManager,
) -> None:
    """Register all handlers with dependencies injected."""

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
        text = (
            f"Session: **{session.name}**\n"
            f"ID: `{session.id}`\n"
            f"Claude Session: `{cli_id}`\n"
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
        _cancel_flags[message.from_user.id] = True
        await message.answer("Cancelling...")

    # Bot-managed commands — everything else (including /plan, /review, etc.) goes to Claude
    _bot_commands = {
        "start", "help", "new", "sessions", "switch",
        "current", "rename", "delete", "cancel",
    }

    def _is_bot_command(text: str) -> bool:
        if not text.startswith("/"):
            return False
        cmd = text.split()[0].lstrip("/").split("@")[0]  # handle /cmd@botname
        return cmd in _bot_commands

    @r.message(F.text)
    async def handle_message(message: Message) -> None:
        if message.text and _is_bot_command(message.text):
            return  # already handled by Command filters above
        assert message.from_user and message.text
        user_id = message.from_user.id

        lock = _get_lock(user_id)
        if lock.locked():
            await message.answer("Processing previous message... please wait.")
            return

        async with lock:
            _cancel_flags[user_id] = False
            session = await session_manager.get_or_create_active(user_id)

            status_msg = await message.answer("Thinking...")
            last_edit = time.monotonic()
            accumulated_text = ""
            last_tool_status = ""

            try:
                async for event in bridge.send_message(
                    prompt=message.text,
                    claude_session_id=session.claude_session_id,
                ):
                    if _cancel_flags.get(user_id):
                        raise asyncio.CancelledError()

                    if event.type == "text":
                        accumulated_text += event.data
                        now = time.monotonic()
                        if now - last_edit >= 2.5:
                            preview = accumulated_text[-3500:]
                            if len(accumulated_text) > 3500:
                                preview = "...\n" + preview
                            try:
                                await status_msg.edit_text(preview or "...")
                            except Exception:
                                pass
                            last_edit = now

                    elif event.type == "tool_use":
                        last_tool_status = event.data
                        try:
                            await status_msg.edit_text(f"⏳ {event.data}")
                        except Exception:
                            pass

                    elif event.type == "tool_result":
                        try:
                            await status_msg.edit_text(f"✓ {last_tool_status}")
                        except Exception:
                            pass

                    elif event.type == "error":
                        await status_msg.edit_text(f"Error: {event.data[:4000]}")
                        return

                    elif event.type == "done":
                        # Capture claude session ID for future --resume
                        if event.session_id:
                            await session_manager.set_claude_session_id(
                                session.id, event.session_id
                            )
                        await session_manager.touch_session(session.id)
                        # Use final text from done event if we have it
                        if event.data and not accumulated_text:
                            accumulated_text = event.data

            except asyncio.CancelledError:
                await status_msg.edit_text("Cancelled.")
                return
            except Exception as e:
                logger.exception("Error processing message")
                await status_msg.edit_text(f"Error: {e}")
                return

            # Send final response
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
