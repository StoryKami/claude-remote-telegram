# claude-remote-telegram

Control Claude remotely via Telegram. Execute commands, read/write files, and manage conversation sessions — all from your phone.

## Features

- **Chat with Claude** — natural language conversation via Telegram
- **Code execution** — Claude can run bash/cmd commands on your server
- **File operations** — read, write, and list files
- **Session management** — multiple persistent conversation sessions
- **Security** — user whitelist, path sandboxing, command filtering
- **Portable** — Docker support, `.env` configuration

## Quick Start

### 1. Prerequisites

- Python 3.11+
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Anthropic API Key

### 2. Setup

```bash
git clone https://github.com/StoryKami/claude-remote-telegram.git
cd claude-remote-telegram

# Create .env from template
cp .env.example .env
# Edit .env with your tokens and user ID

# Install dependencies
pip install -r requirements.txt

# Run
python -m src.main
```

### 3. Docker

```bash
cp .env.example .env
# Edit .env
docker compose up -d
```

## Configuration

See `.env.example` for all options. Required:

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | From @BotFather |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `ALLOWED_USER_IDS` | Comma-separated Telegram user IDs |

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/new [name]` | Create new session |
| `/sessions` | List all sessions |
| `/switch <id>` | Switch to session |
| `/current` | Current session info |
| `/rename <name>` | Rename session |
| `/clear` | Clear session history |
| `/delete <id>` | Delete session |
| `/model [name]` | View/change Claude model |
| `/system [prompt]` | Set custom system prompt |
| `/cancel` | Cancel current request |

## Architecture

```
Telegram → Bot Handler → Claude API Client → Tool Executor → bash/files
                ↕                  ↕
          Auth Middleware    Session Manager (SQLite)
```

## Security

- **User whitelist** — only allowed Telegram user IDs can interact
- **Path sandbox** — file operations restricted to workspace directory
- **Command filter** — dangerous commands (rm -rf /, shutdown, etc.) blocked
- **Timeouts** — bash commands have configurable timeout limits
- **Output limits** — command output and file sizes are capped
