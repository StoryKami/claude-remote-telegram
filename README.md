# claude-remote-telegram

Control your Claude Code sessions remotely via Telegram. Send messages from your phone, Claude Code executes on your server.

## How It Works

```
Telegram → Bot → claude CLI (already authenticated) → Response → Telegram
```

No API key needed — uses your existing Claude Code authentication (API key or OAuth).

## Features

- **Full Claude Code** — bash, file ops, search, agents — everything the CLI can do
- **Session management** — multiple persistent sessions with `--resume`
- **Streaming** — real-time progress updates as Claude works
- **Security** — Telegram user ID whitelist
- **Portable** — works anywhere Claude Code is installed

## Quick Start

### Prerequisites

- Python 3.11+
- Claude Code CLI installed and authenticated (`claude` command works)
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))

### Setup

```bash
git clone https://github.com/StoryKami/claude-remote-telegram.git
cd claude-remote-telegram

cp .env.example .env
# Edit .env: set TELEGRAM_BOT_TOKEN and ALLOWED_USER_IDS

pip install -r requirements.txt
python -m src.main
```

### Docker

```bash
cp .env.example .env
# Edit .env
docker compose up -d
```

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | From @BotFather |
| `ALLOWED_USER_IDS` | Yes | Comma-separated Telegram user IDs |
| `CLAUDE_CLI_PATH` | No | Path to claude CLI (default: `claude`) |
| `CLAUDE_MODEL` | No | Override model (default: CLI default) |
| `CLAUDE_PERMISSION_MODE` | No | Permission mode (default: `bypassPermissions`) |
| `WORKSPACE_DIR` | No | Working directory for Claude (default: `.`) |
| `CLI_TIMEOUT` | No | Max seconds per request (default: 300) |

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/new [name]` | New session (fresh Claude context) |
| `/sessions` | List all sessions |
| `/switch <id>` | Switch to a session |
| `/current` | Current session info |
| `/rename <name>` | Rename session |
| `/delete <id>` | Delete session |
| `/cancel` | Cancel current request |

## Architecture

```
src/
├── main.py              # Entry point
├── config.py            # .env settings
├── bot/
│   ├── handlers.py      # Telegram command & message handlers
│   ├── middleware.py     # Auth middleware
│   ├── formatters.py    # Message splitting for Telegram limits
│   └── commands.py      # Help text
├── claude/
│   └── bridge.py        # Claude CLI subprocess bridge
├── session/
│   ├── manager.py       # Session CRUD
│   ├── repository.py    # SQLite storage
│   └── models.py        # Data models
└── security/
    └── auth.py          # User whitelist
```
