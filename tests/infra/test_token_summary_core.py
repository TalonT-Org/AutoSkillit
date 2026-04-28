"""Tests: token_summary_appender core — existence, early-exit, happy path, session filtering."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.infra._token_summary_helpers import _make_run_skill_event, _run_hook, _write_sessions

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]


def test_tsa1_token_summary_appender_script_exists() -> None:
    """token_summary_hook.py must exist in hooks/ on disk."""
    from autoskillit.core.paths import pkg_root

    assert (pkg_root() / "hooks" / "token_summary_hook.py").exists()


def test_tsa_rest_api_no_gh_pr_commands() -> None:
    """Hook source must not contain 'gh pr edit' or 'gh pr view' subprocess calls."""
    from autoskillit.core.paths import pkg_root

    source = (pkg_root() / "hooks" / "token_summary_hook.py").read_text(encoding="utf-8")
    assert "gh pr edit" not in source, (
        "gh pr edit found in hook — must be replaced with "
        "gh api repos/.../pulls/{N} --method PATCH --field body=..."
    )
    assert "gh pr view" not in source, (
        "gh pr view found in hook — must be replaced with gh api repos/.../pulls/{N} --jq '.body'"
    )


def test_tsa2_no_pr_url_exits_zero() -> None:
    """No GitHub PR URL in tool result → exits 0, no gh subprocess."""
    from autoskillit.core.paths import pkg_root

    hook_path = pkg_root() / "hooks" / "token_summary_hook.py"
    event = _make_run_skill_event("done.\n%%ORDER_UP%%")

    import json

    proc = subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0
    assert "gh" not in proc.stdout


def test_tsa3_no_sessions_jsonl_exits_zero(tmp_path: Path) -> None:
    """Missing sessions.jsonl → exits 0 (valid: no sessions yet)."""
    pr_url = "https://github.com/owner/repo/pull/42"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")

    _, exit_code = _run_hook(event, log_root=tmp_path / "nonexistent")
    assert exit_code == 0


def test_tsa4_no_pipeline_id_sessions_exits_zero(tmp_path: Path) -> None:
    """sessions.jsonl exists but no pipeline_id set → hook skips all sessions → exits 0."""
    log_root = tmp_path / "logs"
    log_root.mkdir()

    _write_sessions(
        log_root,
        [
            {"dir_name": "s1", "cwd": "/some/other/pipeline", "step_name": "plan"},
        ],
    )

    pr_url = "https://github.com/owner/repo/pull/42"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")

    _, exit_code = _run_hook(event, log_root=log_root)
    assert exit_code == 0


def test_tsa5_matching_sessions_formats_table_and_edits_pr(tmp_path: Path) -> None:
    """Matching sessions → aggregate token data and append ## Token Usage Summary."""
    import json

    log_root = tmp_path / "logs"
    log_root.mkdir()
    pipeline_id = "test-pipeline-tsa5"

    _write_sessions(
        log_root,
        [
            {
                "dir_name": "session-1",
                "cwd": "/some/worktree",
                "kitchen_id": pipeline_id,
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
                "kitchen_id": pipeline_id,
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
                "kitchen_id": pipeline_id,
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
    hook_config.write_text(json.dumps({"kitchen_id": pipeline_id}))

    pr_url = "https://github.com/owner/repo/pull/42"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")

    view_result = MagicMock()
    view_result.returncode = 0
    view_result.stdout = "Existing PR body without summary."

    edit_calls: list[list[str]] = []

    def subprocess_side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if "api" in args and "--method" not in args:
            return view_result
        if "api" in args and "--method" in args:
            edit_calls.append(list(args))
            return MagicMock(returncode=0)
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=subprocess_side_effect):
        _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    assert exit_code == 0
    assert len(edit_calls) == 1
    field_idx = edit_calls[0].index("--raw-field")
    body_arg = edit_calls[0][field_idx + 1]
    assert body_arg.startswith("body=")
    body_content = body_arg[len("body=") :]
    assert "## Token Usage Summary" in body_content
    assert "plan" in body_content
    assert "open-pr" in body_content
    assert "**Total**" in body_content
    assert "uncached" in body_content
    assert "cache_read" in body_content
    assert "cache_write" in body_content


def test_tsa6_idempotency_skips_if_summary_present(tmp_path: Path) -> None:
    """PR body already contains ## Token Usage Summary → gh pr edit NOT called."""
    import json

    log_root = tmp_path / "logs"
    log_root.mkdir()
    pipeline_id = "test-pipeline-tsa6"

    _write_sessions(
        log_root,
        [
            {
                "dir_name": "session-1",
                "cwd": "/some/worktree",
                "kitchen_id": pipeline_id,
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
    hook_config.write_text(json.dumps({"kitchen_id": pipeline_id}))

    pr_url = "https://github.com/owner/repo/pull/42"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")

    view_result = MagicMock()
    view_result.returncode = 0
    view_result.stdout = "## Token Usage Summary\n\n| Step | input |...\n"

    edit_calls: list = []

    def subprocess_side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if "api" in args and "--method" in args:
            edit_calls.append(args)
        return view_result

    with patch("subprocess.run", side_effect=subprocess_side_effect):
        _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    assert exit_code == 0
    assert len(edit_calls) == 0


def test_tsa_kitchen_id_match_despite_cwd_mismatch(tmp_path: Path) -> None:
    """kitchen_id match + CWD mismatch → sessions FOUND → hook appends table."""
    import json

    log_root = tmp_path / "logs"
    log_root.mkdir()
    kitchen_id = "test-kitchen-abc123"

    _write_sessions(
        log_root,
        [
            {
                "dir_name": "s1",
                "cwd": "/worktrees/impl-fix",
                "kitchen_id": kitchen_id,
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
    hook_config.write_text(json.dumps({"kitchen_id": kitchen_id}))
    pr_url = "https://github.com/owner/repo/pull/42"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")
    view_result = MagicMock(returncode=0, stdout="Existing PR body.")
    edit_calls: list = []

    def run_side(args, **kwargs):
        if "api" in args and "--method" not in args:
            return view_result
        if "api" in args and "--method" in args:
            edit_calls.append(args)
            return MagicMock(returncode=0)
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=run_side):
        _, exit_code = _run_hook(
            event,
            log_root=log_root,
            hook_config_path=hook_config,
        )
    assert exit_code == 0
    assert len(edit_calls) == 1, "gh api PATCH must be called when kitchen_id matches"


def test_tsa_kitchen_id_mismatch_exits_zero(tmp_path: Path) -> None:
    """Wrong kitchen_id → no sessions found → exits 0, no gh pr edit."""
    import json

    log_root = tmp_path / "logs"
    log_root.mkdir()
    _write_sessions(
        log_root,
        [
            {
                "dir_name": "s1",
                "cwd": "/worktree",
                "kitchen_id": "kitchen-A",
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
    hook_config.write_text(json.dumps({"kitchen_id": "kitchen-B"}))
    event = _make_run_skill_event("pr_url=https://github.com/owner/repo/pull/99\n%%ORDER_UP%%")
    _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)
    assert exit_code == 0


def test_tsa8_gh_pr_edit_failure_exits_nonzero(tmp_path: Path) -> None:
    """gh pr edit returning non-zero → hook exits 0 (fail-open)."""
    import json

    log_root = tmp_path / "logs"
    log_root.mkdir()
    pipeline_id = "test-pipeline-tsa8"

    _write_sessions(
        log_root,
        [
            {
                "dir_name": "session-1",
                "cwd": "/some/worktree",
                "kitchen_id": pipeline_id,
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
    hook_config.write_text(json.dumps({"kitchen_id": pipeline_id}))

    pr_url = "https://github.com/owner/repo/pull/42"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")

    view_result = MagicMock()
    view_result.returncode = 0
    view_result.stdout = "Existing body without summary."

    def subprocess_side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if "api" in args and "--method" not in args:
            return view_result
        if "api" in args and "--method" in args:
            raise subprocess.CalledProcessError(1, args)
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=subprocess_side_effect):
        _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    assert exit_code == 0


def test_tsa_gh_pr_edit_stderr_captured(tmp_path: Path) -> None:
    """gh pr edit failure includes stderr in the logged error message."""
    import json

    log_root = tmp_path / "logs"
    log_root.mkdir()
    pipeline_id = "pipe-edit-test"

    _write_sessions(
        log_root,
        [
            {
                "dir_name": "s1",
                "cwd": "/w",
                "kitchen_id": pipeline_id,
                "step_name": "plan",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 5.0,
            }
        ],
    )

    hook_config = tmp_path / ".autoskillit_hook_config.json"
    hook_config.write_text(json.dumps({"kitchen_id": pipeline_id}))

    pr_url = "https://github.com/owner/repo/pull/1"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")

    view_ok = MagicMock(returncode=0, stdout="Some body.")
    error = subprocess.CalledProcessError(1, ["gh", "api", "repos/owner/repo/pulls/1"])
    error.stderr = "authentication required"

    def run_side(args, **kwargs):
        if "api" in args and "--method" not in args:
            return view_ok
        if "api" in args and "--method" in args:
            raise error
        return MagicMock(returncode=0)

    stderr_output: list[str] = []
    with patch("subprocess.run", side_effect=run_side):
        with patch("sys.stderr") as mock_stderr:
            mock_stderr.write = lambda s: stderr_output.append(s)
            _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    assert exit_code == 0
    combined = "".join(stderr_output)
    assert "authentication required" in combined, (
        "stderr from CalledProcessError must appear in diagnostic output"
    )


def test_tsa_gh_pr_view_failure_emits_diagnostic(tmp_path: Path) -> None:
    """gh pr view non-zero exit emits a stderr message before exiting 0."""
    import json

    log_root = tmp_path / "logs"
    log_root.mkdir()
    pipeline_id = "pipe-view-fail"

    _write_sessions(
        log_root,
        [
            {
                "dir_name": "s1",
                "cwd": "/w",
                "kitchen_id": pipeline_id,
                "step_name": "plan",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 5.0,
            }
        ],
    )

    hook_config = tmp_path / ".autoskillit_hook_config.json"
    hook_config.write_text(json.dumps({"kitchen_id": pipeline_id}))

    pr_url = "https://github.com/owner/repo/pull/2"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")

    view_fail = MagicMock(returncode=1, stderr="HTTP 401 Unauthorized", stdout="")

    stderr_output: list[str] = []
    with patch("subprocess.run", return_value=view_fail):
        with patch("sys.stderr") as mock_stderr:
            mock_stderr.write = lambda s: stderr_output.append(s)
            _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    assert exit_code == 0
    combined = "".join(stderr_output)
    assert combined.strip(), "gh pr view failure must emit a diagnostic to stderr"


def test_token_summary_hook_patch_failure_exits_zero(tmp_path: Path) -> None:
    """CalledProcessError on PATCH must exit 0, not 1 (fail-open hook)."""
    import json

    event = {
        "tool_name": "mcp__autoskillit_server__run_skill",
        "tool_response": json.dumps({"result": json.dumps({"success": True})}),
    }
    pr_event = {
        **event,
        "tool_response": json.dumps(
            {
                "result": json.dumps(
                    {
                        "success": True,
                        "pr_url": "https://github.com/owner/repo/pull/1",
                    }
                )
            }
        ),
    }

    original_run = subprocess.run

    def failing_run(cmd, **kwargs):
        if "PATCH" in (cmd if isinstance(cmd, str) else " ".join(str(c) for c in cmd)):
            raise subprocess.CalledProcessError(1, cmd, stderr="API error")
        return original_run(cmd, **kwargs)

    with patch("autoskillit.hooks.token_summary_hook.subprocess.run", failing_run):
        _, exit_code = _run_hook(
            event=pr_event,
            log_root=tmp_path,
        )
    assert exit_code == 0


def test_token_summary_hook_unexpected_error_exits_zero(monkeypatch: object) -> None:
    """Unhandled exception in outer except must exit 0 (fail-open)."""
    import json

    original_loads = json.loads
    call_count = [0]

    def bomb_loads(s: str) -> object:
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("injected failure")
        return original_loads(s)

    with patch("autoskillit.hooks.token_summary_hook.json.loads", bomb_loads):
        _, exit_code = _run_hook(event={"tool_name": "any", "tool_response": "{}"})

    assert exit_code == 0
