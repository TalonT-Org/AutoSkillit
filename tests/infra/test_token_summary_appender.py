"""Tests for the token_summary_appender PostToolUse hook.

The hook appends a ## Token Usage Summary table to newly-opened PRs by
reading on-disk session logs after every run_skill response.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch


def _run_hook(
    event: dict | None = None,
    raw_stdin: str | None = None,
    log_root: Path | None = None,
    cwd: str | None = None,
    hook_config_path: Path | None = None,
) -> tuple[str, int]:
    """Run token_summary_appender.main() with synthetic stdin.

    Returns (stdout_output, exit_code).

    Args:
        hook_config_path: Path to a hook config JSON file containing ``pipeline_id``.
            When provided, patches ``_read_pipeline_id`` to return the ``pipeline_id``
            value from that file. When absent, ``_read_pipeline_id`` reads from the
            real filesystem (returns '' if no file present in the test CWD).
        cwd: Legacy parameter — patches ``os.getcwd`` for backward compatibility.
            No longer used by the hook after the pipeline_id fix, but retained so
            existing tests (TSA-4) can pass the parameter without error.
    """
    from autoskillit.hooks.token_summary_appender import main

    stdin_text = raw_stdin if raw_stdin is not None else json.dumps(event or {})
    exit_code = 0
    buf = io.StringIO()

    with ExitStack() as stack:
        stack.enter_context(patch("sys.stdin", io.StringIO(stdin_text)))
        stack.enter_context(redirect_stdout(buf))
        if log_root is not None:
            stack.enter_context(
                patch(
                    "autoskillit.hooks.token_summary_appender._log_root",
                    return_value=log_root,
                )
            )
        if cwd is not None:
            stack.enter_context(
                patch(
                    "autoskillit.hooks.token_summary_appender.os.getcwd",
                    return_value=cwd,
                )
            )
        if hook_config_path is not None:
            pipeline_id = json.loads(hook_config_path.read_text(encoding="utf-8")).get(
                "pipeline_id", ""
            )
            stack.enter_context(
                patch(
                    "autoskillit.hooks.token_summary_appender._read_pipeline_id",
                    return_value=pipeline_id,
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
        }
        (session_dir / "token_usage.json").write_text(json.dumps(token_data))


# ---------------------------------------------------------------------------
# TSA-1: hook script exists on disk
# ---------------------------------------------------------------------------


def test_tsa1_token_summary_appender_script_exists() -> None:
    """token_summary_appender.py must exist in hooks/ on disk."""
    from autoskillit.core.paths import pkg_root

    assert (pkg_root() / "hooks" / "token_summary_appender.py").exists()


# ---------------------------------------------------------------------------
# TSA-2: no PR URL in result → exits 0, makes no gh calls
# ---------------------------------------------------------------------------


def test_tsa2_no_pr_url_exits_zero() -> None:
    """No GitHub PR URL in tool result → exits 0, no gh subprocess."""
    from autoskillit.core.paths import pkg_root

    hook_path = pkg_root() / "hooks" / "token_summary_appender.py"
    event = _make_run_skill_event("done.\n%%ORDER_UP%%")
    stdin_text = json.dumps(event)

    proc = subprocess.run(
        [sys.executable, str(hook_path)],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0
    assert "gh" not in proc.stdout


# ---------------------------------------------------------------------------
# TSA-3: sessions.jsonl does not exist → exits 0 silently
# ---------------------------------------------------------------------------


def test_tsa3_no_sessions_jsonl_exits_zero(tmp_path: Path) -> None:
    """Missing sessions.jsonl → exits 0 (valid: no sessions yet)."""
    pr_url = "https://github.com/owner/repo/pull/42"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")

    _, exit_code = _run_hook(event, log_root=tmp_path / "nonexistent")
    assert exit_code == 0


# ---------------------------------------------------------------------------
# TSA-4: no CWD-matching sessions → exits 0 silently
# ---------------------------------------------------------------------------


def test_tsa4_no_cwd_matching_sessions_exits_zero(tmp_path: Path) -> None:
    """sessions.jsonl exists but no entries match current CWD → exits 0."""
    log_root = tmp_path / "logs"
    log_root.mkdir()

    other_cwd = "/some/other/pipeline"
    _write_sessions(
        log_root,
        [
            {"dir_name": "s1", "cwd": other_cwd, "step_name": "plan"},
        ],
    )

    pr_url = "https://github.com/owner/repo/pull/42"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")

    _, exit_code = _run_hook(event, log_root=log_root, cwd="/current/pipeline")
    assert exit_code == 0


# ---------------------------------------------------------------------------
# TSA-5: matching sessions → formats table and calls gh pr edit
# ---------------------------------------------------------------------------


def test_tsa5_matching_sessions_formats_table_and_edits_pr(tmp_path: Path) -> None:
    """Matching sessions → aggregate token data and append ## Token Usage Summary."""
    log_root = tmp_path / "logs"
    log_root.mkdir()
    pipeline_id = "test-pipeline-tsa5"

    # 3 sessions: plan-1, plan-2, open-pr
    _write_sessions(
        log_root,
        [
            {
                "dir_name": "session-1",
                "cwd": "/some/worktree",
                "pipeline_id": pipeline_id,
                "step_name": "plan-1",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 200,
                "timing_seconds": 10.0,
            },
            {
                "dir_name": "session-2",
                "cwd": "/some/worktree",
                "pipeline_id": pipeline_id,
                "step_name": "plan-2",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 200,
                "timing_seconds": 10.0,
            },
            {
                "dir_name": "session-3",
                "cwd": "/some/worktree",
                "pipeline_id": pipeline_id,
                "step_name": "open-pr",
                "input_tokens": 500,
                "output_tokens": 250,
                "cache_creation_input_tokens": 50,
                "cache_read_input_tokens": 100,
                "timing_seconds": 5.0,
            },
        ],
    )

    hook_config = tmp_path / ".autoskillit_hook_config.json"
    hook_config.write_text(json.dumps({"pipeline_id": pipeline_id}))

    pr_url = "https://github.com/owner/repo/pull/42"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")

    view_result = MagicMock()
    view_result.returncode = 0
    view_result.stdout = "Existing PR body without summary."

    edit_calls: list[list[str]] = []

    def subprocess_side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if len(args) >= 3 and args[1] == "pr" and args[2] == "view":
            return view_result
        if len(args) >= 3 and args[1] == "pr" and args[2] == "edit":
            edit_calls.append(list(args))
            return MagicMock(returncode=0)
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=subprocess_side_effect):
        _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    assert exit_code == 0
    assert len(edit_calls) == 1
    body_idx = edit_calls[0].index("--body")
    body_arg = edit_calls[0][body_idx + 1]
    assert "## Token Usage Summary" in body_arg
    # plan-1 and plan-2 should collapse to "plan"
    assert "plan" in body_arg
    # open-pr should be preserved
    assert "open-pr" in body_arg
    # Total row
    assert "**Total**" in body_arg


# ---------------------------------------------------------------------------
# TSA-6: idempotency — body already has ## Token Usage Summary → no edit
# ---------------------------------------------------------------------------


def test_tsa6_idempotency_skips_if_summary_present(tmp_path: Path) -> None:
    """PR body already contains ## Token Usage Summary → gh pr edit NOT called."""
    log_root = tmp_path / "logs"
    log_root.mkdir()
    pipeline_id = "test-pipeline-tsa6"

    _write_sessions(
        log_root,
        [
            {
                "dir_name": "session-1",
                "cwd": "/some/worktree",
                "pipeline_id": pipeline_id,
                "step_name": "plan",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 10.0,
            },
        ],
    )

    hook_config = tmp_path / ".autoskillit_hook_config.json"
    hook_config.write_text(json.dumps({"pipeline_id": pipeline_id}))

    pr_url = "https://github.com/owner/repo/pull/42"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")

    view_result = MagicMock()
    view_result.returncode = 0
    view_result.stdout = "## Token Usage Summary\n\n| Step | input |...\n"

    edit_calls: list = []

    def subprocess_side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if len(args) >= 3 and args[1] == "pr" and args[2] == "edit":
            edit_calls.append(args)
        return view_result

    with patch("subprocess.run", side_effect=subprocess_side_effect):
        _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    assert exit_code == 0
    assert len(edit_calls) == 0


# ---------------------------------------------------------------------------
# TSA-7: step name canonicalization
# ---------------------------------------------------------------------------


def test_tsa7_canonical_step_name() -> None:
    """_canonical strips trailing -N suffix; non-digit suffixes are preserved."""
    from autoskillit.hooks.token_summary_appender import _canonical

    assert _canonical("plan-30") == "plan"
    assert _canonical("open-pr-2") == "open-pr"
    assert _canonical("open-pr") == "open-pr"
    assert _canonical("plan-1") == "plan"
    assert _canonical("") == ""
    assert _canonical("implement") == "implement"


# ---------------------------------------------------------------------------
# TSA-8: gh pr edit failure → exits non-zero
# ---------------------------------------------------------------------------


def test_tsa8_gh_pr_edit_failure_exits_nonzero(tmp_path: Path) -> None:
    """gh pr edit returning non-zero → hook exits non-zero (error surfaced)."""
    log_root = tmp_path / "logs"
    log_root.mkdir()
    pipeline_id = "test-pipeline-tsa8"

    _write_sessions(
        log_root,
        [
            {
                "dir_name": "session-1",
                "cwd": "/some/worktree",
                "pipeline_id": pipeline_id,
                "step_name": "plan",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 5.0,
            },
        ],
    )

    hook_config = tmp_path / ".autoskillit_hook_config.json"
    hook_config.write_text(json.dumps({"pipeline_id": pipeline_id}))

    pr_url = "https://github.com/owner/repo/pull/42"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")

    view_result = MagicMock()
    view_result.returncode = 0
    view_result.stdout = "Existing body without summary."

    def subprocess_side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if len(args) >= 3 and args[1] == "pr" and args[2] == "view":
            return view_result
        if len(args) >= 3 and args[1] == "pr" and args[2] == "edit":
            raise subprocess.CalledProcessError(1, args)
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=subprocess_side_effect):
        _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    assert exit_code != 0


# ---------------------------------------------------------------------------
# TSA-9: pipeline_id match + CWD mismatch → sessions FOUND → hook appends table
# ---------------------------------------------------------------------------


def test_tsa_pipeline_id_match_despite_cwd_mismatch(tmp_path: Path) -> None:
    """pipeline_id match + CWD mismatch → sessions FOUND → hook appends table.

    Production failure mode: hook fires in orchestrator dir, sessions have worktree CWD.
    pipeline_id correlation makes CWD irrelevant.
    """
    log_root = tmp_path / "logs"
    log_root.mkdir()
    pipeline_id = "test-pipeline-abc123"

    _write_sessions(
        log_root,
        [
            {
                "dir_name": "s1",
                "cwd": "/worktrees/impl-fix",
                "pipeline_id": pipeline_id,
                "step_name": "implement",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 20.0,
            }
        ],
    )

    hook_config = tmp_path / ".autoskillit_hook_config.json"
    hook_config.write_text(json.dumps({"pipeline_id": pipeline_id}))
    pr_url = "https://github.com/owner/repo/pull/42"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")
    view_result = MagicMock(returncode=0, stdout="Existing PR body.")
    edit_calls: list = []

    def run_side(args, **kwargs):
        if "view" in args:
            return view_result
        if "edit" in args:
            edit_calls.append(args)
            return MagicMock(returncode=0)
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=run_side):
        _, exit_code = _run_hook(
            event,
            log_root=log_root,
            cwd="/remediation-20260327-080817",  # different from sessions' worktree cwd
            hook_config_path=hook_config,
        )
    assert exit_code == 0
    assert len(edit_calls) == 1, "gh pr edit must be called when pipeline_id matches"


# ---------------------------------------------------------------------------
# TSA-10: pipeline_id mismatch → no sessions found → exits 0, no gh pr edit
# ---------------------------------------------------------------------------


def test_tsa_pipeline_id_mismatch_exits_zero(tmp_path: Path) -> None:
    """Wrong pipeline_id → no sessions found → exits 0, no gh pr edit."""
    log_root = tmp_path / "logs"
    log_root.mkdir()
    _write_sessions(
        log_root,
        [
            {
                "dir_name": "s1",
                "cwd": "/worktree",
                "pipeline_id": "pipeline-A",
                "step_name": "implement",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 10.0,
            }
        ],
    )
    hook_config = tmp_path / ".autoskillit_hook_config.json"
    hook_config.write_text(json.dumps({"pipeline_id": "pipeline-B"}))
    event = _make_run_skill_event("pr_url=https://github.com/owner/repo/pull/99\n%%ORDER_UP%%")
    _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)
    assert exit_code == 0
