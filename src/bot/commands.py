from __future__ import annotations

HELP_TEXT = """
**Claude Remote Telegram**

Send any message to interact with Claude Code remotely. Claude has full access to tools (bash, file ops, etc.) on the server.

**Commands:**
/new `[name]` — New session (fresh context)
/sessions — List sessions
/switch `<id>` — Switch session
/current — Current session info
/rename `<name>` — Rename session
/delete `<id>` — Delete session
/cancel — Cancel current request
/help — This message
""".strip()

WELCOME_TEXT = """
**Claude Remote** 🤖

Your Claude Code session, accessible from Telegram.

Send any message to start. Claude can execute commands, edit files, search code — everything Claude Code can do.

Type /help for commands.
""".strip()
