from __future__ import annotations

import asyncio
import logging
import logging.handlers
import ssl
import sys

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
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

    log_dir = settings.get_db_path().parent
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "bot.log"

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.handlers.RotatingFileHandler(
                log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
            ),
        ],
    )
    logger = logging.getLogger(__name__)

    # Components
    auth_service = AuthService(settings.get_allowed_user_ids())
    bridge = ClaudeBridge(settings)

    repository = SessionRepository(settings.get_db_path())
    await repository.initialize()

    # Skip SSL verification if behind corporate VPN with MITM proxy
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    tg_session = AiohttpSession()
    tg_session._connector_init["ssl"] = ssl_context
    bot = Bot(token=settings.telegram_bot_token, session=tg_session)

    try:
        session_manager = SessionManager(
            repository=repository,
            max_sessions=settings.max_sessions_per_user,
        )

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
            BotCommand(command="model", description="Switch Claude model (opus|sonnet|haiku)"),
            BotCommand(command="cancel", description="Cancel current request"),
            BotCommand(command="revert", description="Revert uncommitted changes"),
            BotCommand(command="local", description="List local Claude sessions"),
            BotCommand(command="pull", description="Git pull + restart"),
            BotCommand(command="restart", description="Restart bot"),
            BotCommand(command="help", description="Help"),
        ])

        # Notify restart completion to the chat that triggered it
        restart_file = settings.get_db_path().parent / ".restart_chat"
        crash_marker = settings.get_db_path().parent / ".crash_marker"
        if restart_file.exists():
            try:
                import json as _json
                data = _json.loads(restart_file.read_text())
                chat_id = data.get("chat_id")
                thread_id = data.get("thread_id")
                auto_reverted = data.get("auto_reverted", False)
                if chat_id:
                    msg = "⚠️ Crash detected after pull/restart — auto-reverted to last commit." if auto_reverted else "Bot restarted."
                    await bot.send_message(
                        chat_id, msg,
                        message_thread_id=thread_id,
                    )
            except Exception:
                pass
            restart_file.unlink(missing_ok=True)
        crash_marker.unlink(missing_ok=True)

        logger.info("Workspace: %s", settings.get_workspace_path())
        logger.info("Bot starting...")
        await dp.start_polling(bot)
    finally:
        await repository.close()
        await bot.session.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    import json as _json
    import subprocess
    import time
    from pathlib import Path as _Path

    RETRY_DELAY = 5  # seconds
    PROJECT_DIR = _Path(__file__).resolve().parent.parent
    CRASH_MARKER = PROJECT_DIR / "data" / ".crash_marker"
    RESTART_CHAT_FILE = PROJECT_DIR / "data" / ".restart_chat"

    def _auto_revert() -> bool:
        """Revert uncommitted changes after a post-pull/restart crash. Returns True if reverted."""
        if not CRASH_MARKER.exists():
            return False
        try:
            marker = _json.loads(CRASH_MARKER.read_text())
        except Exception:
            CRASH_MARKER.unlink(missing_ok=True)
            return False

        if marker.get("reverted"):
            # Already reverted once — don't loop
            CRASH_MARKER.unlink(missing_ok=True)
            return False

        print("[AUTO-REVERT] Post-pull crash detected. Reverting uncommitted changes...", flush=True)
        try:
            diff = subprocess.run(
                ["git", "diff", "--stat", "HEAD"],
                cwd=str(PROJECT_DIR), capture_output=True, text=True, timeout=10,
            )
            print(f"[AUTO-REVERT] Changes:\n{diff.stdout.strip()}", flush=True)

            subprocess.run(
                ["git", "checkout", "."],
                cwd=str(PROJECT_DIR), capture_output=True, timeout=10,
            )
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=str(PROJECT_DIR), capture_output=True, timeout=10,
            )
        except Exception as e:
            print(f"[AUTO-REVERT] Failed: {e}", flush=True)
            CRASH_MARKER.unlink(missing_ok=True)
            return False

        # Mark as reverted so we don't loop
        marker["reverted"] = True
        CRASH_MARKER.write_text(_json.dumps(marker))

        # Notify via Telegram on next successful start
        chat_info = marker.get("chat")
        if chat_info:
            RESTART_CHAT_FILE.parent.mkdir(parents=True, exist_ok=True)
            RESTART_CHAT_FILE.write_text(_json.dumps({
                "chat_id": chat_info.get("chat_id"),
                "thread_id": chat_info.get("thread_id"),
                "auto_reverted": True,
            }))

        print("[AUTO-REVERT] Done. Restarting with clean state...", flush=True)
        return True

    def _set_crash_marker() -> None:
        """Write crash marker if a restart/pull was the last action (restart_chat exists)."""
        if RESTART_CHAT_FILE.exists():
            try:
                chat_data = _json.loads(RESTART_CHAT_FILE.read_text())
                if not chat_data.get("auto_reverted"):
                    CRASH_MARKER.parent.mkdir(parents=True, exist_ok=True)
                    CRASH_MARKER.write_text(_json.dumps({"chat": chat_data}))
            except Exception:
                pass

    attempt = 0

    while True:
        attempt += 1

        # Check for post-pull crash → auto revert
        if attempt > 1:
            _auto_revert()

        # Clear module-level state from previous run (locks tied to old event loop)
        from src.bot import handlers as _h
        _h._session_locks.clear()
        _h._cancel_flags.clear()
        _h._message_queues.clear()
        _h._user_modes.clear()

        # Prevent duplicate logging handlers on restart
        logging.root.handlers.clear()

        try:
            asyncio.run(main())
            # Clean exit — remove crash marker
            CRASH_MARKER.unlink(missing_ok=True)
            break
        except KeyboardInterrupt:
            CRASH_MARKER.unlink(missing_ok=True)
            break
        except Exception as e:
            print(f"[CRASH] Attempt {attempt}: {e}", flush=True)
            _set_crash_marker()
            print(f"[RESTART] Restarting in {RETRY_DELAY}s...", flush=True)
            time.sleep(RETRY_DELAY)
