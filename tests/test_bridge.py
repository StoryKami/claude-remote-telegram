"""Tests for bridge utilities."""
from src.claude.bridge import _describe_tool, _short_path, _short_bash


class TestShortPath:
    def test_short_path_already_short(self):
        assert _short_path("file.py") == "file.py"

    def test_short_path_two_segments(self):
        assert _short_path("src/file.py") == "src/file.py"

    def test_short_path_long(self):
        result = _short_path("F:\\Programming\\claude_workspace\\src\\bot\\handlers.py")
        assert result == "bot/handlers.py"

    def test_short_path_unix(self):
        result = _short_path("/home/user/projects/bot/main.py")
        assert result == "bot/main.py"


class TestShortBash:
    def test_simple_command(self):
        assert _short_bash("ls -la") == "ls -la"

    def test_long_command_truncated(self):
        cmd = "a" * 50
        result = _short_bash(cmd)
        assert len(result) <= 40
        assert result.endswith("...")

    def test_wsl_wrapper_stripped(self):
        cmd = 'wsl -d Ubuntu -e bash -c "ssh root@192.168.50.50 ls"'
        result = _short_bash(cmd)
        assert not result.startswith("wsl")
        assert "ssh" in result

    def test_ssh_wrapper_stripped(self):
        cmd = 'ssh -i key root@host bash -c "cd /tmp && ls"'
        result = _short_bash(cmd)
        assert result.startswith("cd /tmp")


class TestDescribeTool:
    def test_bash(self):
        result = _describe_tool("Bash", {"command": "git status"})
        assert result == "bash: git status"

    def test_read(self):
        result = _describe_tool("Read", {"file_path": "/long/path/to/file.py"})
        assert "file.py" in result

    def test_write(self):
        result = _describe_tool("Write", {"file_path": "src/main.py"})
        assert result == "write: src/main.py"

    def test_edit(self):
        result = _describe_tool("Edit", {"file_path": "src/bot/handlers.py"})
        assert "handlers.py" in result

    def test_glob(self):
        result = _describe_tool("Glob", {"pattern": "**/*.py"})
        assert result == "glob: **/*.py"

    def test_grep(self):
        result = _describe_tool("Grep", {"pattern": "async def"})
        assert result == "grep: async def"

    def test_agent(self):
        result = _describe_tool("Agent", {"description": "search code"})
        assert result == "agent: search code"

    def test_websearch(self):
        result = _describe_tool("WebSearch", {"query": "python asyncio"})
        assert result == "search: python asyncio"

    def test_unknown(self):
        result = _describe_tool("CustomTool", {})
        assert result == "customtool"
