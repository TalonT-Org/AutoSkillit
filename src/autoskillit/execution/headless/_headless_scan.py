"""Write-path scanning utilities for headless session stdout JSONL.

Extracted from headless.py to keep that module below the 1100-line
architectural limit (REQ-CNST-010-E2).

IL-1 module (execution/). Imports session._session_model for record discrimination.
"""

from __future__ import annotations

import json
import os

from autoskillit.core import extract_bash_write_targets
from autoskillit.execution.headless._headless_path_tokens import _is_path_outside_cwd
from autoskillit.execution.session._session_model import _is_parent_assistant_record


def _scan_jsonl_write_paths(
    stdout: str,
    cwd: str,
    *,
    write_tool_names: frozenset[str] = frozenset({"Write", "Edit"}),
    bash_tool_name: str = "Bash",
) -> list[str]:
    """Scan raw JSONL stdout for Write/Edit/Bash tool calls outside cwd.

    Parses assistant records from the JSONL stream and extracts file_path
    arguments from Write and Edit tool_use blocks, plus absolute paths from
    Bash commands. Returns warning strings for any path outside cwd.

    Non-blocking: caller decides whether to surface or suppress warnings.
    Returns [] when stdout is empty or cwd is empty/relative.
    """
    if not stdout.strip() or not cwd or not os.path.isabs(cwd):
        return []

    warnings: list[str] = []

    for raw_line in stdout.strip().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or not _is_parent_assistant_record(obj):
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

            # Bash-specific branch must take precedence over the generic file_path
            # branch: write_guard_tool_names may include Bash, but Bash writes are
            # extracted from the command string, not from input.file_path.
            if tool_name == bash_tool_name:
                command = inputs.get("command", "")
                if isinstance(command, str):
                    targets = extract_bash_write_targets(command, cwd)
                    for path in targets:
                        if _is_path_outside_cwd(path, cwd):
                            warnings.append(
                                f"Bash command contained write target '{path}'"
                                f" outside session cwd '{cwd}'"
                            )
            elif tool_name in write_tool_names:
                file_path = inputs.get("file_path", "")
                if isinstance(file_path, str) and _is_path_outside_cwd(file_path, cwd):
                    warnings.append(
                        f"{tool_name} tool targeted '{file_path}' outside session cwd '{cwd}'"
                    )

    return warnings
