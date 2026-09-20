#!/usr/bin/env python3
"""PostToolUse hook: runs ruff lint on Python files after Edit/Write in headless sessions."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
_RUNTIME_DIR = str(Path(_HOOKS_DIR) / "_runtime")
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)


from _hook_settings import is_headless_session  # type: ignore[import-not-found]  # noqa: E402

_IMPLEMENT_PREFIXES = ("implement-", "resolve-")
LINT_AUTOFIX_TRIGGER = "--- RUFF AUTOFIX ---"
LINT_ERROR_TRIGGER = "--- RUFF LINT ---"

_TIMEOUT_S = 15


def _resolve_ruff() -> str:
    """Resolve ruff binary co-located with the running Python interpreter."""
    candidate = Path(sys.executable).parent / "ruff"
    if candidate.is_file():
        return str(candidate)
    return "ruff"


def _file_sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _run_ruff_pipeline(file_path: str) -> tuple[bool, str]:
    try:
        hash_before = _file_sha256(file_path)
    except OSError:
        return False, ""

    ruff_cmd = _resolve_ruff()

    try:
        subprocess.run(
            [ruff_cmd, "format", file_path],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        pass
    except (FileNotFoundError, OSError):
        return False, ""

    try:
        subprocess.run(
            [ruff_cmd, "check", "--fix", "--ignore", "F4", file_path],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False, ""

    remaining_errors = ""
    try:
        result = subprocess.run(
            [ruff_cmd, "check", file_path],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
        )
        if result.returncode != 0 and result.stdout.strip():
            remaining_errors = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    try:
        hash_after = _file_sha256(file_path)
    except OSError:
        return False, ""

    return hash_before != hash_after, remaining_errors


def main() -> None:
    if not is_headless_session():
        sys.exit(0)

    skill_name = os.environ.get("AUTOSKILLIT_SKILL_NAME", "")
    if not any(skill_name.startswith(p) for p in _IMPLEMENT_PREFIXES):
        sys.exit(0)

    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        sys.exit(0)
    file_path = tool_input.get("file_path", "")

    if not file_path or not file_path.endswith(".py"):
        sys.exit(0)

    if not Path(file_path).is_file():
        sys.exit(0)

    file_changed, remaining_errors = _run_ruff_pipeline(file_path)

    messages: list[str] = []

    if file_changed:
        messages.append(
            f"{LINT_AUTOFIX_TRIGGER}\n"
            "ruff auto-formatted this file. Re-read it before your next Edit "
            "to avoid stale old_string mismatches."
        )

    if remaining_errors:
        messages.append(
            f"{LINT_ERROR_TRIGGER}\n"
            "ruff found errors that --fix cannot resolve. "
            f"Fix these before proceeding:\n{remaining_errors}"
        )

    if not messages:
        sys.exit(0)

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "updatedToolResult": "\n\n".join(messages),
                }
            }
        )
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
