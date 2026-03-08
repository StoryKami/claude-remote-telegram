from __future__ import annotations

HELP_TEXT = """
**Claude Remote Telegram**

Send any message to chat with Claude. Claude can execute commands, read/write files on the server.

**Commands:**
/new `[name]` — New session
/sessions — List sessions
/switch `<id>` — Switch session
/current — Current session info
/rename `<name>` — Rename session
/clear — Clear session history
/delete `<id>` — Delete session
/model `[name]` — View/change model
/system `[prompt]` — Set system prompt
/cancel — Cancel current request
/help — This message
""".strip()

WELCOME_TEXT = """
Welcome to **Claude Remote**!

I'm your remote coding assistant. Send me any message to start chatting with Claude.

Claude can:
- Execute bash commands on the server
- Read and write files
- Help with coding tasks

Type /help for all commands.
""".strip()
