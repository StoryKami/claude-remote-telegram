from __future__ import annotations


def get_tool_definitions() -> list[dict]:
    return [
        {
            "name": "bash",
            "description": (
                "Execute a bash command on the server. "
                "The working directory is the session workspace. "
                "Long-running commands may timeout."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 30, max: 300)",
                    },
                },
                "required": ["command"],
            },
        },
        {
            "name": "read_file",
            "description": (
                "Read the contents of a file. "
                "Returns numbered lines. "
                "Use offset and limit for large files."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path or path relative to workspace",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Starting line number (0-based, default: 0)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max lines to read (default: 2000)",
                    },
                },
                "required": ["path"],
            },
        },
        {
            "name": "write_file",
            "description": (
                "Write content to a file. "
                "Creates the file if it doesn't exist, overwrites if it does. "
                "Parent directories are created automatically."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path or path relative to workspace",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write",
                    },
                },
                "required": ["path", "content"],
            },
        },
        {
            "name": "list_directory",
            "description": (
                "List files and subdirectories in a directory. "
                "Shows file sizes and modification dates."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path (default: workspace root)",
                    },
                },
                "required": [],
            },
        },
    ]
