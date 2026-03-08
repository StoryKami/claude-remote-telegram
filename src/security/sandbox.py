from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

BLOCKED_PATTERNS: list[str] = [
    r"\brm\s+-rf\s+/\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bpoweroff\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r":\(\)\s*\{",  # fork bomb
    r"\bformat\s+[a-zA-Z]:",  # Windows format
    r"\bdel\s+/[sfq]",  # Windows del
]

_blocked_re = re.compile("|".join(BLOCKED_PATTERNS), re.IGNORECASE)


class Sandbox:
    def __init__(self, workspace_dir: Path, max_file_size: int = 1048576) -> None:
        self._workspace = workspace_dir.resolve()
        self._max_file_size = max_file_size

    @property
    def workspace(self) -> Path:
        return self._workspace

    def validate_path(self, path: str) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = self._workspace / p
        resolved = p.resolve()
        if not self._is_within_workspace(resolved):
            raise PermissionError(
                f"Path '{resolved}' is outside workspace '{self._workspace}'"
            )
        return resolved

    def validate_command(self, command: str) -> None:
        match = _blocked_re.search(command)
        if match:
            raise PermissionError(f"Blocked command pattern: '{match.group()}'")

    def validate_file_size(self, size: int) -> None:
        if size > self._max_file_size:
            raise ValueError(
                f"File size {size} exceeds limit {self._max_file_size} bytes"
            )

    def _is_within_workspace(self, path: Path) -> bool:
        try:
            path.relative_to(self._workspace)
            return True
        except ValueError:
            return False
