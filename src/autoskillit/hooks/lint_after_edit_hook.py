#!/usr/bin/env python3
"""PostToolUse hook: runs ruff on edited Python files in headless sessions."""

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


from _hook_output import add_context  # noqa: E402
from _hook_payload import edit_target_paths  # noqa: E402
from _hook_settings import enforce_session_scope  # noqa: E402

_IMPLEMENT_PREFIXES = ("implement-", "resolve-")
LINT_AUTOFIX_TRIGGER = "--- RUFF AUTOFIX ---"
LINT_ERROR_TRIGGER = "--- RUFF LINT ---"
RUFF_SOURCE_SUFFIXES: tuple[str, ...] = (".py", ".pyi")

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
    enforce_session_scope("headless_only")

    skill_name = os.environ.get("AUTOSKILLIT_SKILL_NAME", "")
    if not any(skill_name.startswith(p) for p in _IMPLEMENT_PREFIXES):
        sys.exit(0)

    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)

    paths = [
        path
        for path in edit_target_paths(data)
        if path.endswith(RUFF_SOURCE_SUFFIXES) and Path(path).is_file()
    ]
    if not paths:
        sys.exit(0)

    messages: list[str] = []
    multiple_paths = len(paths) > 1
    for file_path in paths:
        file_changed, remaining_errors = _run_ruff_pipeline(file_path)
        prefix = f"{file_path}: " if multiple_paths else ""

        if file_changed:
            messages.append(
                f"{prefix}{LINT_AUTOFIX_TRIGGER}\n"
                "ruff auto-formatted this file. Re-read it before your next Edit "
                "to avoid stale old_string mismatches."
            )

        if remaining_errors:
            messages.append(
                f"{prefix}{LINT_ERROR_TRIGGER}\n"
                "ruff found errors that --fix cannot resolve. "
                f"Fix these before proceeding:\n{remaining_errors}"
            )

    if not messages:
        sys.exit(0)

    add_context("PostToolUse", "\n\n".join(messages))


if __name__ == "__main__":
    main()
