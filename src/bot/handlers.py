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
from src.bot.formatters import (
    format_session_info,
    format_telegram_message,
    format_tool_status,
)

if TYPE_CHECKING:
    from src.claude.client import ClaudeClient
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
    claude_client: ClaudeClient,
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
            await message.answer(f"Created session: **{session.name}** (`{session.id}`)", parse_mode="Markdown")
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
        msg_count = await session_manager.get_message_count(session.id)
        info = format_session_info(
            session.id, session.name, session.model, msg_count, session.is_active
        )
        await message.answer(f"```\n{info}\n```", parse_mode="Markdown")

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

    @r.message(Command("clear"))
    async def cmd_clear(message: Message) -> None:
        assert message.from_user
        session = await session_manager.get_or_create_active(message.from_user.id)
        await session_manager.clear_messages(session.id)
        await message.answer("Session history cleared.")

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

    @r.message(Command("model"))
    async def cmd_model(message: Message) -> None:
        assert message.from_user
        model_name = (message.text or "").replace("/model", "").strip()
        session = await session_manager.get_or_create_active(message.from_user.id)
        if not model_name:
            await message.answer(f"Current model: `{session.model}`", parse_mode="Markdown")
            return
        await session_manager.set_model(session.id, model_name)
        await message.answer(f"Model changed to: `{model_name}`", parse_mode="Markdown")

    @r.message(Command("system"))
    async def cmd_system(message: Message) -> None:
        assert message.from_user
        prompt = (message.text or "").replace("/system", "").strip()
        session = await session_manager.get_or_create_active(message.from_user.id)
        if not prompt:
            current = session.system_prompt or "(default)"
            await message.answer(f"System prompt: {current}")
            return
        await session_manager.set_system_prompt(session.id, prompt)
        await message.answer("System prompt updated.")

    @r.message(Command("cancel"))
    async def cmd_cancel(message: Message) -> None:
        assert message.from_user
        _cancel_flags[message.from_user.id] = True
        await message.answer("Cancelling...")

    @r.message(F.text & ~F.text.startswith("/"))
    async def handle_message(message: Message) -> None:
        assert message.from_user and message.text
        user_id = message.from_user.id

        lock = _get_lock(user_id)
        if lock.locked():
            await message.answer("Processing previous message... please wait.")
            return

        async with lock:
            _cancel_flags[user_id] = False
            session = await session_manager.get_or_create_active(user_id)

            # Send typing indicator
            status_msg = await message.answer("Thinking...")

            last_edit = time.monotonic()
            accumulated_text = ""

            async def on_tool_use(name: str, params: dict) -> None:
                if _cancel_flags.get(user_id):
                    raise asyncio.CancelledError()
                status = format_tool_status(name, params)
                try:
                    await status_msg.edit_text(status)
                except Exception:
                    pass

            try:
                async for event in claude_client.stream_message(
                    session_id=session.id,
                    user_message=message.text,
                    model=session.model,
                    system_prompt=session.system_prompt,
                    on_tool_use=on_tool_use,
                ):
                    if _cancel_flags.get(user_id):
                        raise asyncio.CancelledError()

                    if event.type == "text":
                        accumulated_text += event.data
                        now = time.monotonic()
                        if now - last_edit >= 2.0:
                            preview = accumulated_text[:4000]
                            if len(accumulated_text) > 4000:
                                preview += "\n..."
                            try:
                                await status_msg.edit_text(preview or "...")
                            except Exception:
                                pass
                            last_edit = now

                    elif event.type in ("tool_use", "tool_result"):
                        try:
                            await status_msg.edit_text(event.data)
                        except Exception:
                            pass

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

            # Edit first chunk into status message
            try:
                await status_msg.edit_text(chunks[0])
            except Exception:
                await message.answer(chunks[0])

            # Send remaining chunks as new messages
            for chunk in chunks[1:]:
                await message.answer(chunk)
