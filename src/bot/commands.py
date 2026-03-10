from __future__ import annotations

HELP_TEXT = """
**Claude Remote Telegram**

Send any message to interact with Claude Code remotely. Claude has full access to tools (bash, file ops, etc.) on the server.

**Bot Commands:**
/new `[name]` — New session (fresh context)
/sessions — List sessions
/switch `<id>` — Switch session
/current — Current session info
/rename `<name>` — Rename session
/delete `<id>` — Delete session
/cancel — Cancel current request
/revert — Revert uncommitted changes (git reset)
/mode `[plan|code]` — View/switch mode
/local — Local Claude sessions (resume any)
/pull — Git pull + restart (원격 배포)
/restart — Restart bot
/help — This message

**Media:**
사진, 스크린샷, 파일(PDF 등)을 보내면 Claude가 분석합니다.
작업 중에도 메시지를 보내면 큐에 쌓여 순차 처리됩니다.

**Claude Skills (pass-through):**
/plan, /review, /simplify, /verify, /tdd, /commit\\_push\\_pr, /frontend, /spec, /handoff, /build\\_fix, /techdebt 등
위 명령어는 Claude Code에 그대로 전달됩니다.
""".strip()

WELCOME_TEXT = """
**Claude Remote** 🤖

Your Claude Code session, accessible from Telegram.

Send any message to start. Claude can execute commands, edit files, search code — everything Claude Code can do.

Type /help for commands.
""".strip()
