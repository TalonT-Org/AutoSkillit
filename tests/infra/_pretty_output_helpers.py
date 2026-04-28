"""Shared helpers for pretty_output hook tests."""

from __future__ import annotations

import io
import json
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

# Realistic recipe YAML that mirrors actual bundled recipes.
# Must include an `ingredients:` block to reproduce the raw/derived duplication scenario.
# All formatter tests using `content` should reference this constant.
REALISTIC_RECIPE_YAML = """\
name: implementation
description: Full implementation pipeline
autoskillit_version: "0.3.0"
ingredients:
  task:
    description: What to implement
    required: true
  source_dir:
    description: Path to source directory
    default: ""
  review_approach:
    description: Run review-approach before planning
    default: "false"
kitchen_rules:
  - Always commit before merging
steps:
  implement:
    tool: run_skill
    skill_command: /implement
"""


def _run_hook(
    event: dict | None = None,
    raw_stdin: str | None = None,
    cwd: Path | None = None,
) -> tuple[str, int]:
    """Run pretty_output.main() with synthetic stdin.

    Returns (stdout_output, exit_code).
    """
    from autoskillit.hooks.pretty_output_hook import main

    stdin_text = raw_stdin if raw_stdin is not None else json.dumps(event or {})

    exit_code = 0
    buf = io.StringIO()

    with ExitStack() as stack:
        stack.enter_context(patch("sys.stdin", io.StringIO(stdin_text)))
        stack.enter_context(redirect_stdout(buf))
        if cwd is not None:
            stack.enter_context(
                patch("autoskillit.hooks.pretty_output_hook.Path.cwd", return_value=cwd)
            )
        try:
            main()
        except SystemExit as e:
            exit_code = e.code if e.code is not None else 0

    return buf.getvalue(), exit_code


def _make_run_skill_event(
    success: bool = True,
    result: str = "Done.",
    session_id: str = "abc",
    subtype: str = "end_turn",
    is_error: bool = False,
    exit_code: int = 0,
    needs_retry: bool = False,
    retry_reason: str = "none",
    stderr: str = "",
    token_usage: dict | None = None,
    worktree_path: str = "",
) -> dict:
    return {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__run_skill",
        "tool_response": json.dumps(
            {
                "success": success,
                "result": result,
                "session_id": session_id,
                "subtype": subtype,
                "is_error": is_error,
                "exit_code": exit_code,
                "needs_retry": needs_retry,
                "retry_reason": retry_reason,
                "stderr": stderr,
                "token_usage": token_usage,
                "worktree_path": worktree_path,
            }
        ),
    }


def _wrap_for_claude_code(payload: dict) -> str:
    """Simulate Claude Code's PostToolUse wrapping of MCP text content."""
    return json.dumps({"result": json.dumps(payload)})


def _wrap_plain_str_for_claude_code(text: str) -> str:
    """Simulate Claude Code's PostToolUse wrapping of a plain-text MCP response."""
    return json.dumps({"result": text})


def _make_event(tool_name: str, payload: dict) -> dict:
    """Build a minimal PostToolUse hook event for a given tool and payload dict."""
    return {
        "tool_name": f"mcp__autoskillit__{tool_name}",
        "tool_response": json.dumps(payload),
    }
