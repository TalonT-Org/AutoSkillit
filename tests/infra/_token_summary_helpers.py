"""Shared helpers for token_summary_appender hook tests."""

from __future__ import annotations

import io
import json
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch


def _run_hook(
    event: dict | None = None,
    raw_stdin: str | None = None,
    log_root: Path | None = None,
    hook_config_path: Path | None = None,
) -> tuple[str, int]:
    """Run token_summary_appender.main() with synthetic stdin."""
    from autoskillit.hooks.token_summary_hook import main

    stdin_text = raw_stdin if raw_stdin is not None else json.dumps(event or {})
    exit_code = 0
    buf = io.StringIO()

    with ExitStack() as stack:
        stack.enter_context(patch("sys.stdin", io.StringIO(stdin_text)))
        stack.enter_context(redirect_stdout(buf))
        if log_root is not None:
            stack.enter_context(
                patch(
                    "autoskillit.hooks.token_summary_hook._log_root",
                    return_value=log_root,
                )
            )
        if hook_config_path is not None:
            cfg_data = json.loads(hook_config_path.read_text(encoding="utf-8"))
            kitchen_id = cfg_data.get("kitchen_id") or cfg_data.get("pipeline_id", "")
            stack.enter_context(
                patch(
                    "autoskillit.hooks.token_summary_hook._read_kitchen_id",
                    return_value=kitchen_id,
                )
            )
        try:
            main()
        except SystemExit as e:
            exit_code = e.code if e.code is not None else 0

    return buf.getvalue(), exit_code


def _make_run_skill_event(result_text: str = "Done.\n%%ORDER_UP%%") -> dict:
    """Create a double-wrapped PostToolUse event for run_skill."""
    inner = {"result": result_text, "success": True}
    outer = {"result": json.dumps(inner)}
    return {
        "tool_name": "mcp__autoskillit_server__run_skill",
        "tool_response": json.dumps(outer),
    }


def _write_sessions(log_root: Path, entries: list[dict]) -> None:
    """Write sessions.jsonl and token_usage.json files for test setup."""
    (log_root / "sessions.jsonl").write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    for entry in entries:
        dir_name = entry["dir_name"]
        session_dir = log_root / "sessions" / dir_name
        session_dir.mkdir(parents=True, exist_ok=True)
        token_data = {
            "step_name": entry.get("step_name", "unknown"),
            "input_tokens": entry.get("input_tokens", 1000),
            "output_tokens": entry.get("output_tokens", 500),
            "cache_creation_input_tokens": entry.get("cache_creation_input_tokens", 100),
            "cache_read_input_tokens": entry.get("cache_read_input_tokens", 200),
            "timing_seconds": entry.get("timing_seconds", 10.0),
            "loc_insertions": entry.get("loc_insertions", 0),
            "loc_deletions": entry.get("loc_deletions", 0),
        }
        (session_dir / "token_usage.json").write_text(json.dumps(token_data))
