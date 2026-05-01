"""Write-path scanning utilities for headless session stdout JSONL.

Extracted from headless.py to keep that module below the 1100-line
architectural limit (REQ-CNST-010-E2).

IL-1 module (execution/). No autoskillit imports — stdlib only.
"""

from __future__ import annotations

import json
import os
import re

_WRITE_TOOL_NAMES: frozenset[str] = frozenset({"Write", "Edit"})
_BASH_TOOL_NAME: str = "Bash"
_ABS_PATH_PATTERN: re.Pattern[str] = re.compile(r'(?:^|[\s="\'])(/(?:[a-zA-Z0-9._/~@+:-]+))')
# Exclude paths of 4 chars or fewer (/tmp, /etc, /bin, /var) as low-signal noise.
_MIN_BASH_PATH_LEN: int = 5


def _scan_jsonl_write_paths(stdout: str, cwd: str) -> list[str]:
    """Scan raw JSONL stdout for Write/Edit/Bash tool calls outside cwd.

    Parses assistant records from the JSONL stream and extracts file_path
    arguments from Write and Edit tool_use blocks, plus absolute paths from
    Bash commands. Returns warning strings for any path outside cwd.

    Non-blocking: caller decides whether to surface or suppress warnings.
    Returns [] when stdout is empty or cwd is empty/relative.
    """
    if not stdout.strip() or not cwd or not os.path.isabs(cwd):
        return []

    cwd_prefix = cwd.rstrip("/") + "/"
    warnings: list[str] = []

    for raw_line in stdout.strip().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "assistant":
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_name = block.get("name", "")
            inputs = block.get("input") or {}
            if not isinstance(inputs, dict):
                continue

            if tool_name in _WRITE_TOOL_NAMES:
                file_path = inputs.get("file_path", "")
                if (
                    isinstance(file_path, str)
                    and os.path.isabs(file_path)
                    and not file_path.startswith(cwd_prefix)
                    and file_path != cwd.rstrip("/")
                ):
                    warnings.append(
                        f"{tool_name} tool targeted '{file_path}' outside session cwd '{cwd}'"
                    )

            elif tool_name == _BASH_TOOL_NAME:
                command = inputs.get("command", "")
                if isinstance(command, str):
                    for match in _ABS_PATH_PATTERN.finditer(command):
                        path = match.group(1)
                        if (
                            len(path) >= _MIN_BASH_PATH_LEN
                            and not path.startswith(cwd_prefix)
                            and path != cwd.rstrip("/")
                        ):
                            warnings.append(
                                f"Bash command contained path '{path}' outside session cwd '{cwd}'"
                            )

    return warnings
