from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from src.bot.handlers import router, setup_handlers
from src.bot.middleware import AuthMiddleware
from src.claude.bridge import ClaudeBridge
from src.config import load_settings
from src.security.auth import AuthService
from src.session.manager import SessionManager
from src.session.repository import SessionRepository


async def main() -> None:
    settings = load_settings()

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logger = logging.getLogger(__name__)

    # Components
    auth_service = AuthService(settings.get_allowed_user_ids())
    bridge = ClaudeBridge(settings)

    repository = SessionRepository(settings.get_db_path())
    await repository.initialize()

    session_manager = SessionManager(
        repository=repository,
        max_sessions=settings.max_sessions_per_user,
    )

    # Bot
    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()

    dp.message.middleware(AuthMiddleware(auth_service))
    dp.callback_query.middleware(AuthMiddleware(auth_service))

    setup_handlers(router, bridge, session_manager, settings.get_workspace_path())
    dp.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="new", description="New session (fresh context)"),
        BotCommand(command="sessions", description="List sessions"),
        BotCommand(command="switch", description="Switch session"),
        BotCommand(command="current", description="Current session info"),
        BotCommand(command="rename", description="Rename session"),
        BotCommand(command="delete", description="Delete session"),
        BotCommand(command="mode", description="View/switch mode (plan|code)"),
        BotCommand(command="cancel", description="Cancel current request"),
        BotCommand(command="local", description="List local Claude sessions"),
        BotCommand(command="pull", description="Git pull + restart"),
        BotCommand(command="restart", description="Restart bot"),
        BotCommand(command="help", description="Help"),
    ])

    # Notify users that bot has (re)started
    for uid in settings.get_allowed_user_ids():
        try:
            await bot.send_message(uid, "Bot restarted.")
        except Exception:
            pass

    logger.info("Workspace: %s", settings.get_workspace_path())
    logger.info("Bot starting...")
    try:
        await dp.start_polling(bot)
    finally:
        await repository.close()
        await bot.session.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())
