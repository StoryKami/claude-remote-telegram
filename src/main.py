from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher

from src.bot.handlers import router, setup_handlers
from src.bot.middleware import AuthMiddleware
from src.claude.client import ClaudeClient
from src.claude.executor import ToolExecutor
from src.config import load_settings
from src.security.auth import AuthService
from src.security.sandbox import Sandbox
from src.session.manager import SessionManager
from src.session.repository import SessionRepository


async def main() -> None:
    settings = load_settings()

    # Logging
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logger = logging.getLogger(__name__)

    # Workspace
    workspace = settings.get_workspace_path()
    workspace.mkdir(parents=True, exist_ok=True)
    logger.info("Workspace: %s", workspace)

    # Components
    auth_service = AuthService(settings.get_allowed_user_ids())
    sandbox = Sandbox(workspace, settings.max_file_size)

    repository = SessionRepository(settings.get_db_path())
    await repository.initialize()

    session_manager = SessionManager(
        repository=repository,
        max_sessions=settings.max_sessions_per_user,
        max_messages=settings.session_max_messages,
        default_model=settings.claude_model,
    )

    tool_executor = ToolExecutor(sandbox, settings)

    claude_client = ClaudeClient(
        settings=settings,
        session_manager=session_manager,
        tool_executor=tool_executor,
    )

    # Bot
    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()

    # Middleware
    dp.message.middleware(AuthMiddleware(auth_service))
    dp.callback_query.middleware(AuthMiddleware(auth_service))

    # Handlers
    setup_handlers(router, claude_client, session_manager)
    dp.include_router(router)

    logger.info("Bot starting...")
    try:
        await dp.start_polling(bot)
    finally:
        await repository.close()
        await bot.session.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())
