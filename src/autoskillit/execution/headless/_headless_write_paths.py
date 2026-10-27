"""Write-path advisories from parent-assistant tool calls."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from autoskillit.core import destination_location, is_parent_assistant_record
from autoskillit.execution.headless._headless_path_tokens import _is_path_outside_cwd
from autoskillit.hooks import UNRESOLVED_WRITE_TARGET_REMEDIATION, scan_write_targets


def _tool_write_path_warnings(
    tool_name: Any,
    inputs: dict[str, Any],
    cwd: str,
    write_tool_names: frozenset[str],
    bash_tool_name: str,
) -> list[str]:
    warnings: list[str] = []
    if tool_name == bash_tool_name:
        command = inputs.get("command", "")
        if isinstance(command, str):
            scan = scan_write_targets(command, cwd)
            unresolved = scan.unresolved or not scan.parseable
            paths = [(path, cwd) for path in scan.targets]
            for path in scan.entry_targets:
                try:
                    location = str(destination_location(Path(path)))
                    physical_cwd = str(Path(cwd).resolve()) if os.path.isabs(cwd) else cwd
                    paths.append((location, physical_cwd))
                except (OSError, RuntimeError, ValueError):
                    unresolved = True
            if unresolved:
                warnings.append(
                    "Bash command contained a write target that could not be statically resolved. "
                    + UNRESOLVED_WRITE_TARGET_REMEDIATION
                )
            for path, comparison_cwd in paths:
                if _is_path_outside_cwd(path, comparison_cwd):
                    normalized = os.path.normpath(path)
                    warnings.append(
                        f"Bash command contained write target '{normalized}'"
                        f" outside session cwd '{cwd}'"
                    )
    elif tool_name in write_tool_names:
        file_path = inputs.get("file_path", "")
        if isinstance(file_path, str) and _is_path_outside_cwd(file_path, cwd):
            warnings.append(f"{tool_name} tool targeted '{file_path}' outside session cwd '{cwd}'")
    return warnings


def _scan_jsonl_write_paths(
    stdout: str,
    cwd: str,
    *,
    write_tool_names: frozenset[str] = frozenset({"Write", "Edit"}),
    bash_tool_name: str = "Bash",
) -> list[str]:
    """Scan raw JSONL stdout for Write/Edit/Bash tool calls outside cwd."""
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
        if not isinstance(obj, dict) or not is_parent_assistant_record(obj):
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
            warnings.extend(
                _tool_write_path_warnings(tool_name, inputs, cwd, write_tool_names, bash_tool_name)
            )
    return warnings
