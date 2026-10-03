"""Tests: token_summary_appender core — existence, early-exit, happy path, session filtering."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import production_interpreter_env
from tests.infra._token_summary_helpers import _make_run_skill_event, _run_hook, _write_sessions

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]


def test_tsa2_no_pr_url_exits_zero() -> None:
    """No GitHub PR URL in tool result → exits 0, no gh subprocess."""
    from autoskillit.core.paths import pkg_root

    hook_path = pkg_root() / "hooks" / "token_summary_hook.py"
    event = _make_run_skill_event("done.\n%%ORDER_UP%%")

    proc = subprocess.run(
        [sys.executable, str(hook_path)],
        env=production_interpreter_env(),
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
                "model_identifier": "claude-sonnet-4-6",
                "loc_insertions": 10,
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_write_tokens": 100,
                "cache_read_tokens": 200,
                "timing_seconds": 10.0,
            },
            {
                "dir_name": "session-2",
                "cwd": "/some/worktree",
                "kitchen_id": pipeline_id,
                "step_name": "plan-2",
                "model_identifier": "claude-sonnet-4-6",
                "loc_insertions": 10,
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_write_tokens": 100,
                "cache_read_tokens": 200,
                "timing_seconds": 10.0,
            },
            {
                "dir_name": "session-3",
                "cwd": "/some/worktree",
                "kitchen_id": pipeline_id,
                "step_name": "open-pr",
                "model_identifier": "claude-sonnet-4-6",
                "loc_insertions": 5,
                "input_tokens": 500,
                "output_tokens": 250,
                "cache_write_tokens": 50,
                "cache_read_tokens": 100,
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
    assert body_arg == (
        "body=Existing PR body without summary.\n\n## Token Usage Summary\n\n"
        "| Step | Model | count | uncached | output | cache_read | peak_ctx | turns"
        " | cache_write | time |\n"
        "|------|-------|-------|----------|--------|------------|----------|-------"
        "|-------------|------|\n"
        "| plan (unknown/unknown) | claude-sonnet-4-6 | 2 | 2.0k | 1.0k | 400"
        " | unknown | 0 | 200 | 20s |\n"
        "| open-pr (unknown/unknown) | claude-sonnet-4-6 | 1 | 500 | 250 | 100"
        " | unknown | 0 | 50 | 5s |\n"
        "| **Total (unknown/unknown)** | | | 2.5k | 1.2k | 500 | unknown | | 250 | 25s |\n\n"
        "## Token Efficiency\n\n"
        "| Step | LoC Changed | cache_read/LoC | cache_write/LoC | output/LoC |\n"
        "|------|-------------|----------------|-----------------|------------|\n"
        "| plan (unknown/unknown) | 20 | 20.0 | 10.0 | 50.0 |\n"
        "| open-pr (unknown/unknown) | 5 | 20.0 | 10.0 | 50.0 |\n"
        "| **Total (unknown/unknown)** | **25** | 20.0 | 10.0 | 50.0 |\n\n"
        "## Model Usage Breakdown\n\n"
        "| Model | steps | uncached | output | cache_read | cache_write | time |\n"
        "|-------|-------|----------|--------|------------|-------------|------|\n"
        "| unknown/unknown: claude-sonnet-4-6 | 2 | 2.5k | 1.2k | 500 | 250 | 25s |"
    )


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
                "kitchen_id": pipeline_id,
                "step_name": "plan",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_write_tokens": 0,
                "cache_read_tokens": 0,
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
                "cache_write_tokens": 0,
                "cache_read_tokens": 0,
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
                "cache_write_tokens": 0,
                "cache_read_tokens": 0,
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
                "cache_write_tokens": 0,
                "cache_read_tokens": 0,
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
                "cache_write_tokens": 0,
                "cache_read_tokens": 0,
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
                "cache_write_tokens": 0,
                "cache_read_tokens": 0,
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


def test_efficiency_table_equivalence() -> None:
    """format_efficiency_table and hook _format_efficiency_table produce identical output."""
    from autoskillit.hooks.token_summary_hook import _format_efficiency_table
    from autoskillit.pipeline.telemetry_fmt import TelemetryFormatter

    steps_data = [
        {
            "step_name": "investigate",
            "peak_context": 500,
            "cache_read_tokens": 8000,
            "cache_write_tokens": 2000,
            "output_tokens": 1000,
            "loc_insertions": 30,
            "loc_deletions": 10,
        },
        {
            "step_name": "implement",
            "peak_context": 12000,
            "cache_read_tokens": 50000,
            "cache_write_tokens": 15000,
            "output_tokens": 8000,
            "loc_insertions": 200,
            "loc_deletions": 50,
        },
    ]

    canonical_total = {
        "loc_insertions": sum(s["loc_insertions"] for s in steps_data),
        "loc_deletions": sum(s["loc_deletions"] for s in steps_data),
        "peak_context": max(s["peak_context"] for s in steps_data),
        "cache_read_tokens": sum(s["cache_read_tokens"] for s in steps_data),
        "cache_write_tokens": sum(s["cache_write_tokens"] for s in steps_data),
        "output_tokens": sum(s["output_tokens"] for s in steps_data),
    }
    aggregated = {s["step_name"]: dict(s) for s in steps_data}

    canonical_output = TelemetryFormatter.format_efficiency_table(
        list(steps_data), canonical_total
    )
    hook_output = _format_efficiency_table(aggregated)

    assert canonical_output == hook_output, (
        f"Canonical and hook efficiency tables differ:\n"
        f"CANONICAL:\n{canonical_output}\n\nHOOK:\n{hook_output}"
    )


def test_token_table_equivalence_non_anthropic() -> None:
    """Token table equivalence holds when non-Anthropic models are present."""
    from autoskillit.hooks.token_summary_hook import _format_table
    from autoskillit.pipeline.telemetry_fmt import TelemetryFormatter

    steps_data = [
        {
            "step_name": "plan",
            "model": "claude-sonnet-4-6",
            "input_tokens": 7000,
            "output_tokens": 5939,
            "cache_write_tokens": 8495,
            "cache_read_tokens": 252179,
            "peak_context": 45000,
            "turn_count": 8,
            "invocation_count": 1,
            "elapsed_seconds": 45.0,
        },
        {
            "step_name": "implement",
            "model": "MiniMax-M2.7-highspeed",
            "input_tokens": 2031000,
            "output_tokens": 122306,
            "cache_write_tokens": 280601,
            "cache_read_tokens": 19071323,
            "peak_context": 890000,
            "turn_count": 42,
            "invocation_count": 3,
            "elapsed_seconds": 492.0,
        },
    ]

    canonical_total = {
        "input_tokens": sum(s["input_tokens"] for s in steps_data),
        "output_tokens": sum(s["output_tokens"] for s in steps_data),
        "cache_write_tokens": sum(s["cache_write_tokens"] for s in steps_data),
        "cache_read_tokens": sum(s["cache_read_tokens"] for s in steps_data),
        "peak_context": max(s["peak_context"] for s in steps_data),
        "total_elapsed_seconds": sum(s["elapsed_seconds"] for s in steps_data),
    }
    aggregated = {s["step_name"]: dict(s) for s in steps_data}

    canonical_output = TelemetryFormatter.format_token_table(list(steps_data), canonical_total)
    hook_output = _format_table(aggregated)

    assert canonical_output == hook_output
    assert "implement*" in canonical_output
    assert "non-Anthropic provider" in canonical_output
    assert "plan*" not in canonical_output


def test_token_table_equivalence() -> None:
    """Canonical format_token_table and hook _format_table produce identical output."""
    from autoskillit.hooks.token_summary_hook import _format_table
    from autoskillit.pipeline.telemetry_fmt import TelemetryFormatter

    steps_data = [
        {
            "step_name": "investigate",
            "model": "claude-sonnet-4-6",
            "input_tokens": 7000,
            "output_tokens": 5939,
            "cache_write_tokens": 8495,
            "cache_read_tokens": 252179,
            "peak_context": 45000,
            "turn_count": 8,
            "invocation_count": 1,
            "elapsed_seconds": 45.0,
        },
        {
            "step_name": "implement",
            "model": "claude-sonnet-4-6",
            "input_tokens": 2031000,
            "output_tokens": 122306,
            "cache_write_tokens": 280601,
            "cache_read_tokens": 19071323,
            "peak_context": 890000,
            "turn_count": 42,
            "invocation_count": 3,
            "elapsed_seconds": 492.0,
        },
    ]

    canonical_total = {
        "input_tokens": sum(s["input_tokens"] for s in steps_data),
        "output_tokens": sum(s["output_tokens"] for s in steps_data),
        "cache_write_tokens": sum(s["cache_write_tokens"] for s in steps_data),
        "cache_read_tokens": sum(s["cache_read_tokens"] for s in steps_data),
        "peak_context": max(s["peak_context"] for s in steps_data),
        "total_elapsed_seconds": sum(s["elapsed_seconds"] for s in steps_data),
    }
    aggregated = {s["step_name"]: dict(s) for s in steps_data}

    canonical_output = TelemetryFormatter.format_token_table(list(steps_data), canonical_total)
    hook_output = _format_table(aggregated)

    assert canonical_output == hook_output, (
        f"Canonical and hook token tables differ:\n"
        f"CANONICAL:\n{canonical_output}\n\nHOOK:\n{hook_output}"
    )


def test_format_table_includes_model_column() -> None:
    """Hook _format_table includes Model column and value."""
    from autoskillit.hooks.token_summary_hook import _format_table

    aggregated = {
        "plan": {
            "step_name": "plan",
            "model": "claude-sonnet-4-6",
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_write_tokens": 0,
            "cache_read_tokens": 200,
            "elapsed_seconds": 60.0,
            "invocation_count": 1,
            "loc_insertions": 0,
            "loc_deletions": 0,
            "peak_context": 0,
            "turn_count": 5,
        }
    }
    table = _format_table(aggregated)
    assert "| Model |" in table
    assert "claude-sonnet-4-6" in table


def test_hook_format_model_table() -> None:
    """Hook _format_model_table produces per-model aggregate table."""
    from autoskillit.hooks.token_summary_hook import _format_model_table

    aggregated = {
        "plan": {
            "step_name": "plan",
            "model": "claude-sonnet-4-6",
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_write_tokens": 0,
            "cache_read_tokens": 0,
            "elapsed_seconds": 30.0,
            "invocation_count": 1,
        },
        "implement": {
            "step_name": "implement",
            "model": "MiniMax-M2.7",
            "input_tokens": 500,
            "output_tokens": 200,
            "cache_write_tokens": 0,
            "cache_read_tokens": 0,
            "elapsed_seconds": 60.0,
            "invocation_count": 1,
        },
    }
    table = _format_model_table(aggregated)
    assert "## Model Usage Breakdown" in table
    assert "| Model | steps | uncached | output | cache_read | cache_write | time |" in table
    assert "| claude-sonnet-4-6 | 1 | 100 | 50 | 0 | 0 | 30s |" in table
    assert "| MiniMax-M2.7 | 1 | 500 | 200 | 0 | 0 | 1m 0s |" in table
    table_lines = [ln for ln in table.splitlines() if ln.startswith("|")]
    assert len(table_lines) == 4  # header + separator + 2 model rows


def test_hook_format_model_table_no_model_returns_empty() -> None:
    """Hook _format_model_table returns '' when all entries have no model."""
    from autoskillit.hooks.token_summary_hook import _format_model_table

    aggregated = {
        "plan": {
            "step_name": "plan",
            "model": "",
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_write_tokens": 0,
            "cache_read_tokens": 0,
            "elapsed_seconds": 30.0,
        }
    }
    assert _format_model_table(aggregated) == ""


def test_load_sessions_reads_model_identifier(tmp_path: Path) -> None:
    """Hook _load_sessions populates model field from model_identifier in token_usage.json."""
    from autoskillit.hooks.token_summary_hook import _load_sessions

    kitchen_id = "test-kitchen-t10"
    log_root = tmp_path / "logs"
    log_root.mkdir()

    sessions_dir = log_root / "sessions" / "s1"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "token_usage.json").write_text(
        json.dumps(
            {
                "session_label": "plan",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_write_tokens": 0,
                "cache_read_tokens": 0,
                "timing_seconds": 10.0,
                "model_identifier": "claude-sonnet-4-6",
                "schema_version": 2,
            }
        )
    )
    (log_root / "sessions.jsonl").write_text(
        json.dumps(
            {
                "session_id": "s1",
                "dir_name": "s1",
                "kitchen_id": kitchen_id,
                "timestamp": "2026-01-01T00:00:00Z",
            }
        )
        + "\n"
    )

    aggregated = _load_sessions(log_root, kitchen_id)
    assert any(entry["step_name"] == "plan" for entry in aggregated.values())
    assert next(iter(aggregated.values()))["model"] == "claude-sonnet-4-6"


def test_model_table_equivalence() -> None:
    """_format_model_table (hook) and format_model_table (canonical) produce equivalent output."""
    from autoskillit.hooks.token_summary_hook import _format_model_table
    from autoskillit.pipeline.telemetry_fmt import TelemetryFormatter

    aggregated = {
        "plan": {
            "step_name": "plan",
            "model": "claude-sonnet-4-6",
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_write_tokens": 100,
            "cache_read_tokens": 200,
            "elapsed_seconds": 60.0,
            "invocation_count": 1,
        },
        "implement": {
            "step_name": "implement",
            "model": "MiniMax-M2.7",
            "input_tokens": 5000,
            "output_tokens": 2000,
            "cache_write_tokens": 500,
            "cache_read_tokens": 1000,
            "elapsed_seconds": 120.0,
            "invocation_count": 2,
        },
    }

    hook_table = _format_model_table(aggregated)

    model_totals = [
        {
            "model": "claude-sonnet-4-6",
            "step_count": 1,
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_write_tokens": 100,
            "cache_read_tokens": 200,
            "elapsed_seconds": 60.0,
        },
        {
            "model": "MiniMax-M2.7",
            "step_count": 1,
            "input_tokens": 5000,
            "output_tokens": 2000,
            "cache_write_tokens": 500,
            "cache_read_tokens": 1000,
            "elapsed_seconds": 120.0,
        },
    ]
    canonical_table = TelemetryFormatter.format_model_table(model_totals)

    assert hook_table == canonical_table, (
        f"Hook and canonical model tables diverged:"
        f"\nHOOK:\n{hook_table}\n\nCANONICAL:\n{canonical_table}"
    )


def test_pr_telemetry_assembly_parity() -> None:
    """Hook inline assembly and TelemetryFormatter.format_pr_telemetry_block agree on sections."""
    from autoskillit.hooks.token_summary_hook import (
        _format_efficiency_table,
        _format_model_table,
        _format_table,
    )
    from autoskillit.pipeline.telemetry_fmt import TelemetryFormatter

    aggregated = {
        "plan": {
            "step_name": "plan",
            "model": "claude-sonnet-4-6",
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_write_tokens": 100,
            "cache_read_tokens": 200,
            "peak_context": {"state": "unknown", "value": None},
            "elapsed_seconds": 60.0,
            "invocation_count": 1,
            "loc_insertions": 50,
            "loc_deletions": 10,
        },
        "implement": {
            "step_name": "implement",
            "model": "claude-sonnet-4-6",
            "input_tokens": 5000,
            "output_tokens": 2000,
            "cache_write_tokens": 500,
            "cache_read_tokens": 1000,
            "peak_context": {"state": "unknown", "value": None},
            "elapsed_seconds": 120.0,
            "invocation_count": 2,
            "loc_insertions": 200,
            "loc_deletions": 30,
        },
    }

    hook_token = _format_table(aggregated)
    hook_efficiency = _format_efficiency_table(aggregated)
    hook_model = _format_model_table(aggregated)
    hook_parts = [hook_token]
    if hook_efficiency:
        hook_parts.append(hook_efficiency)
    if hook_model:
        hook_parts.append(hook_model)
    hook_assembly = "\n\n".join(hook_parts)

    steps = list(aggregated.values())
    total = {
        "input_tokens": sum(s["input_tokens"] for s in steps),
        "output_tokens": sum(s["output_tokens"] for s in steps),
        "cache_write_tokens": sum(s["cache_write_tokens"] for s in steps),
        "cache_read_tokens": sum(s["cache_read_tokens"] for s in steps),
        "peak_context": {"state": "unknown", "value": None},
        "total_elapsed_seconds": sum(s["elapsed_seconds"] for s in steps),
        "loc_insertions": sum(s["loc_insertions"] for s in steps),
        "loc_deletions": sum(s["loc_deletions"] for s in steps),
    }
    model_totals = [
        {
            "model": "claude-sonnet-4-6",
            "step_count": 2,
            "input_tokens": 6000,
            "output_tokens": 2500,
            "cache_write_tokens": 600,
            "cache_read_tokens": 1200,
            "elapsed_seconds": 180.0,
        },
    ]
    canonical = TelemetryFormatter.format_pr_telemetry_block(steps, total, model_totals)

    assert hook_assembly == canonical, (
        f"Assembly mismatch:\nHOOK:\n{hook_assembly}\nCANONICAL:\n{canonical}"
    )


class TestLoadSessionsSchemaVersionCompat:
    """Backward compatibility: _load_sessions reads v1-v3 token_usage.json."""

    _KITCHEN_ID = "test-kitchen-compat"

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(
                {
                    "session_label": "plan",
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_creation_input_tokens": 10,
                    "cache_read_input_tokens": 5,
                    "timing_seconds": 30.0,
                },
                id="v1-legacy-keys",
            ),
            pytest.param(
                {
                    "session_label": "plan",
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_write_tokens": 10,
                    "cache_read_tokens": 5,
                    "timing_seconds": 30.0,
                    "schema_version": 2,
                },
                id="v2-canonical-keys",
            ),
            pytest.param(
                {
                    "session_label": "plan",
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_write_tokens": 10,
                    "cache_read_tokens": 5,
                    "timing_seconds": 30.0,
                    "turn_usage_file": "turn_usage.jsonl",
                    "turn_usage_count": 2,
                    "turn_usage_schema_version": 1,
                    "schema_version": 3,
                },
                id="v3-turn-usage-descriptor",
            ),
        ],
    )
    def test_loads_supported_schema_versions(self, tmp_path: Path, payload: dict) -> None:
        from autoskillit.hooks.token_summary_hook import _load_sessions

        log_root = tmp_path / "logs"
        session_dir = log_root / "sessions" / "s001"
        session_dir.mkdir(parents=True)
        (session_dir / "token_usage.json").write_text(json.dumps(payload))
        (log_root / "sessions.jsonl").write_text(
            json.dumps(
                {
                    "dir_name": "s001",
                    "kitchen_id": self._KITCHEN_ID,
                    "timestamp": "2026-05-01T00:00:00+00:00",
                }
            )
            + "\n"
        )
        result = _load_sessions(log_root, self._KITCHEN_ID)
        assert len(result) == 1
        entry = next(iter(result.values()))
        assert entry["step_name"] == "plan"
        assert entry["cache_write_tokens"] == {"state": "measured", "value": 10}
        assert entry["cache_read_tokens"] == {"state": "measured", "value": 5}


# ---------------------------------------------------------------------------
# T-HOOK-EFF: Token Efficiency table in hook output
# ---------------------------------------------------------------------------


# T-HOOK-EFF-1
def test_efficiency_table_appended_when_loc_present(tmp_path: Path) -> None:
    """Hook appends efficiency table after token table when LoC data exists."""
    log_root = tmp_path / "logs"
    log_root.mkdir()
    kitchen_id = "kitchen-eff-1"

    _write_sessions(
        log_root,
        [
            {
                "dir_name": "s1",
                "cwd": "/w",
                "kitchen_id": kitchen_id,
                "step_name": "implement",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_write_tokens": 100,
                "cache_read_tokens": 200,
                "timing_seconds": 10.0,
                "loc_insertions": 80,
                "loc_deletions": 20,
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
        _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    assert exit_code == 0
    assert len(edit_calls) == 1
    body_arg = next(a for a in edit_calls[0] if a.startswith("body="))
    body_content = body_arg[len("body=") :]
    assert "## Token Efficiency" in body_content
    assert "cache_read/LoC" in body_content
    assert "cache_write/LoC" in body_content
    assert "output/LoC" in body_content
    assert "peak_ctx/LoC" not in body_content
    eff_section = body_content[body_content.index("## Token Efficiency") :]
    eff_header = eff_section.split("\n")[2]
    assert "Step" in eff_header
    assert "LoC Changed" in eff_header


# T-HOOK-EFF-2
def test_efficiency_table_omitted_when_no_loc_data(tmp_path: Path) -> None:
    """Hook omits efficiency table when all sessions have loc_insertions=loc_deletions=0."""
    log_root = tmp_path / "logs"
    log_root.mkdir()
    kitchen_id = "kitchen-eff-2"

    _write_sessions(
        log_root,
        [
            {
                "dir_name": "s1",
                "cwd": "/w",
                "kitchen_id": kitchen_id,
                "step_name": "plan",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_write_tokens": 100,
                "cache_read_tokens": 200,
                "timing_seconds": 10.0,
                "loc_insertions": 0,
                "loc_deletions": 0,
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
        _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    assert exit_code == 0
    assert len(edit_calls) == 1
    body_arg = next(a for a in edit_calls[0] if a.startswith("body="))
    body_content = body_arg[len("body=") :]
    assert "## Token Efficiency" not in body_content


# T-HOOK-EFF-3
def test_efficiency_table_zero_loc_step_shows_dash(tmp_path: Path) -> None:
    """Steps with zero LoC changed show — in the hook-generated efficiency table."""
    log_root = tmp_path / "logs"
    log_root.mkdir()
    kitchen_id = "kitchen-eff-3"

    _write_sessions(
        log_root,
        [
            {
                "dir_name": "s1",
                "cwd": "/w",
                "kitchen_id": kitchen_id,
                "step_name": "plan",
                "input_tokens": 500,
                "output_tokens": 100,
                "cache_write_tokens": 50,
                "cache_read_tokens": 100,
                "timing_seconds": 5.0,
                "loc_insertions": 0,
                "loc_deletions": 0,
            },
            {
                "dir_name": "s2",
                "cwd": "/w",
                "kitchen_id": kitchen_id,
                "step_name": "implement",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_write_tokens": 100,
                "cache_read_tokens": 200,
                "timing_seconds": 10.0,
                "loc_insertions": 50,
                "loc_deletions": 10,
            },
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
        _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    assert exit_code == 0
    assert len(edit_calls) == 1
    body_arg = next(a for a in edit_calls[0] if a.startswith("body="))
    body_content = body_arg[len("body=") :]
    assert "## Token Efficiency" in body_content
    eff_section = body_content[body_content.index("## Token Efficiency") :]
    plan_row = next(
        (line for line in eff_section.split("\n") if line.startswith("| plan (")),
        None,
    )
    assert plan_row is not None, "No source-pair plan row found in efficiency section"
    assert "—" in plan_row


def test_non_anthropic_session_shows_provider_model_name(tmp_path: Path) -> None:
    """Non-Anthropic sessions must show the effective provider model, not the Anthropic alias."""
    from autoskillit.hooks.token_summary_hook import _format_table, _load_sessions

    kitchen_id = "kitchen-non-anthropic-001"
    log_root = tmp_path / "logs"
    log_root.mkdir()
    _write_sessions(
        log_root,
        [
            {
                "session_id": "s1",
                "dir_name": "s1",
                "timestamp": "2026-06-01T00:00:00Z",
                "kitchen_id": kitchen_id,
                "model_identifier": "MiniMax-M2.7-highspeed",
                "configured_model": "sonnet",
                "profile_name": "minimax",
                "input_tokens": 1000,
                "output_tokens": 500,
            }
        ],
    )

    aggregated = _load_sessions(log_root, kitchen_id)
    assert aggregated, "Expected at least one aggregated session entry"

    step_entry = next(iter(aggregated.values()))
    assert step_entry["model"] == "MiniMax-M2.7-highspeed"
    assert step_entry["model"] != "sonnet"

    table = _format_table(aggregated)
    assert "MiniMax-M2.7-highspeed" in table
    assert "sonnet" not in table.split("## Token Usage Summary")[1].split("|")[2]
    assert "*" in table


@pytest.mark.parametrize(
    "scenario,model_identifier,configured_model,profile_name,expected_model",
    [
        ("resolved-first", "claude-sonnet-5[1m]", "opus[1m]", "", "claude-sonnet-5[1m]"),
        ("configured-only", None, "sonnet", "anthropic", "sonnet"),
        (
            "non-anthropic",
            "MiniMax-M2.7-highspeed",
            "sonnet",
            "minimax",
            "MiniMax-M2.7-highspeed",
        ),
    ],
)
def test_reader_agreement_contract(
    tmp_path: Path,
    scenario,
    model_identifier,
    configured_model,
    profile_name,
    expected_model,
) -> None:
    """Both readers must agree and non-Anthropic sessions must not show Anthropic aliases."""
    from autoskillit.hooks.token_summary_hook import _load_sessions
    from autoskillit.pipeline.tokens import DefaultTokenLog

    kitchen_id = f"kitchen-rac-{scenario}"
    log_root = tmp_path / f"rac-logs-{scenario}"
    log_root.mkdir()
    entry = {
        "session_id": f"rac-{scenario}",
        "dir_name": f"rac-{scenario}",
        "timestamp": "2026-06-01T00:00:00Z",
        "kitchen_id": kitchen_id,
        "configured_model": configured_model,
        "profile_name": profile_name,
        "input_tokens": 100,
        "output_tokens": 50,
    }
    if model_identifier is not None:
        entry["model_identifier"] = model_identifier
    _write_sessions(log_root, [entry])

    disk_log = DefaultTokenLog()
    disk_log.load_from_log_dir(log_root)
    disk_entries = disk_log.get_report()
    assert disk_entries
    disk_model = disk_entries[0]["model"]

    hook_agg = _load_sessions(log_root, kitchen_id)
    assert hook_agg
    hook_model = next(iter(hook_agg.values()))["model"]

    assert disk_model == hook_model, f"readers disagree: disk={disk_model!r}, hook={hook_model!r}"
    assert disk_model == expected_model
    if profile_name == "minimax":
        assert not disk_model.startswith("claude-")


def test_load_sessions_handles_null_cache_write(tmp_path: Path) -> None:
    """_load_sessions must handle null cache_write_tokens in token_usage.json without crash."""
    from autoskillit.hooks.token_summary_hook import _load_sessions

    log_root = tmp_path / "logs"
    log_root.mkdir()
    pipeline_id = "test-null-cache"

    _write_sessions(
        log_root,
        [
            {
                "dir_name": "s1",
                "cwd": "/some/worktree",
                "kitchen_id": pipeline_id,
                "step_name": "plan",
            },
        ],
    )
    sess_dir = log_root / "sessions" / "s1"
    sess_dir.mkdir(parents=True, exist_ok=True)
    (sess_dir / "token_usage.json").write_text(
        json.dumps(
            {
                "session_label": "plan",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_write_tokens": None,
                "cache_read_tokens": None,
                "timing_seconds": 0.0,
            }
        )
    )

    result = _load_sessions(log_root, pipeline_id)
    assert len(result) == 1
    entry = next(iter(result.values()))
    assert entry["step_name"] == "plan"
    assert entry["cache_write_tokens"] == {"state": "unknown", "value": None}
    assert entry["cache_read_tokens"] == {"state": "unknown", "value": None}


@pytest.fixture
def token_summary_source_corpus() -> dict[str, dict]:
    """Small full-table corpus: repeated step/model, partial sources, and explicit unknown."""
    entries = [
        ("both-missing", {}, 100, 10, 20, 40, 100, 1, 10.0, 1),
        ("provider-missing", {"backend": "codex"}, 200, 20, 40, 80, 200, 2, 20.0, 2),
        ("backend-missing", {"provider_used": "openai"}, 300, 30, 60, 120, 300, 3, 30.0, 3),
        (
            "explicit-unknown",
            {"backend": "unknown", "provider_used": "unknown"},
            400,
            40,
            80,
            160,
            400,
            4,
            40.0,
            4,
        ),
    ]
    return {
        key: {
            "step_name": "implement",
            **source,
            "model": "shared-model",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_write_tokens": cache_write_tokens,
            "cache_read_tokens": cache_read_tokens,
            "peak_context": peak_context,
            "turn_count": turns,
            "invocation_count": 1,
            "elapsed_seconds": elapsed,
            "loc_insertions": loc,
            "loc_deletions": 0,
        }
        for (
            key,
            source,
            input_tokens,
            output_tokens,
            cache_write_tokens,
            cache_read_tokens,
            peak_context,
            turns,
            elapsed,
            loc,
        ) in entries
    }


@pytest.mark.parametrize(
    "steps,raws,expected_rows",
    [
        (
            ["plan-1", "plan-2"],
            [100, {"state": "unavailable", "value": None}],
            [
                "| plan (unknown/unknown) | 30 | unknown | unknown | unknown |",
                "| **Total (unknown/unknown)** | **30** | unknown | unknown | unknown |",
            ],
        ),
        (
            ["plan", "implement"],
            [100, {"state": "unavailable", "value": None}],
            [
                "| plan (unknown/unknown) | 10 | 10.0 | 10.0 | 10.0 |",
                "| implement (unknown/unknown) | 20 | unavailable | unavailable | unavailable |",
                "| **Total (unknown/unknown)** | **30** | 10.0 | 10.0 | 10.0 |",
            ],
        ),
        (
            ["plan", "implement"],
            [None, 100],
            [
                "| plan (unknown/unknown) | 10 | unknown | unknown | unknown |",
                "| implement (unknown/unknown) | 20 | 5.0 | 5.0 | 5.0 |",
                "| **Total (unknown/unknown)** | **30** | unknown | unknown | unknown |",
            ],
        ),
        (
            ["plan", "implement"],
            [{"state": "unavailable", "value": None}, {"state": "not_applicable", "value": None}],
            [
                "| plan (unknown/unknown) | 10 | unavailable | unavailable | unavailable |",
                "| implement (unknown/unknown) | 20 | not_applicable | not_applicable"
                " | not_applicable |",
                "| **Total (unknown/unknown)** | **30** | — | — | — |",
            ],
        ),
    ],
    ids=["strict-step", "eligible-denominator", "unknown-propagation", "all-excluded"],
)
def test_efficiency_bucket_eligibility_goldens(tmp_path, steps, raws, expected_rows):
    from autoskillit.hooks.token_summary_hook import _format_efficiency_table, _load_sessions
    from autoskillit.pipeline.telemetry_fmt import TelemetryFormatter

    _write_sessions(
        tmp_path,
        [
            {
                "dir_name": f"s{i}",
                "kitchen_id": "ratio-corpus",
                "step_name": step,
                "output_tokens": raw,
                "cache_read_tokens": raw,
                "cache_write_tokens": raw,
                "loc_insertions": loc,
            }
            for i, (step, raw, loc) in enumerate(zip(steps, raws, [10, 20], strict=True))
        ],
    )
    aggregated = _load_sessions(tmp_path, "ratio-corpus")
    table = _format_efficiency_table(aggregated)
    assert table.splitlines()[4:] == expected_rows
    assert table == TelemetryFormatter.format_efficiency_table(list(aggregated.values()), {})


def test_absent_and_zero_loc_have_identical_efficiency_projection(tmp_path):
    from autoskillit.hooks.token_summary_hook import _format_efficiency_table, _load_sessions

    _write_sessions(
        tmp_path,
        [
            {
                "dir_name": name,
                "kitchen_id": "zero-loc",
                "step_name": name,
                "output_tokens": 100,
                "cache_read_tokens": 100,
                "cache_write_tokens": 100,
                "loc_insertions": loc,
            }
            for name, loc in [("plan", 0), ("implement", 10)]
        ],
    )
    explicit = _format_efficiency_table(_load_sessions(tmp_path, "zero-loc"))
    path = tmp_path / "sessions" / "plan" / "token_usage.json"
    payload = json.loads(path.read_text())
    del payload["loc_insertions"], payload["loc_deletions"]
    path.write_text(json.dumps(payload))
    absent = _format_efficiency_table(_load_sessions(tmp_path, "zero-loc"))
    assert absent == explicit
    assert absent.splitlines()[4:] == [
        "| plan (unknown/unknown) | 0 | — | — | — |",
        "| implement (unknown/unknown) | 10 | 10.0 | 10.0 | 10.0 |",
        "| **Total (unknown/unknown)** | **10** | 20.0 | 20.0 | 20.0 |",
    ]


def test_source_corpus_preserves_full_tables_and_pr_assembly(
    token_summary_source_corpus: dict[str, dict],
) -> None:
    from autoskillit.hooks.token_summary_hook import (
        _format_efficiency_table,
        _format_model_table,
        _format_table,
    )
    from autoskillit.pipeline.telemetry_fmt import TelemetryFormatter

    steps = list(token_summary_source_corpus.values())
    total = {
        "input_tokens": 1000,
        "output_tokens": 100,
        "cache_write_tokens": 200,
        "cache_read_tokens": 400,
        "peak_context": 400,
        "total_elapsed_seconds": 100.0,
        "loc_insertions": 10,
        "loc_deletions": 0,
    }
    models = [
        {
            "model": "shared-model",
            "step_count": 1,
            "input_tokens": 600,
            "output_tokens": 60,
            "cache_write_tokens": 120,
            "cache_read_tokens": 240,
            "elapsed_seconds": 60.0,
        },
        {
            "model": "unknown/unknown: shared-model",
            "step_count": 1,
            "input_tokens": 400,
            "output_tokens": 40,
            "cache_write_tokens": 80,
            "cache_read_tokens": 160,
            "elapsed_seconds": 40.0,
        },
    ]
    hook_tables = (
        _format_table(token_summary_source_corpus),
        _format_efficiency_table(token_summary_source_corpus),
        _format_model_table(token_summary_source_corpus),
    )
    token_golden = (
        "## Token Usage Summary\n\n"
        "| Step | Model | count | uncached | output | cache_read | peak_ctx | turns"
        " | cache_write | time |\n"
        "|------|-------|-------|----------|--------|------------|----------|-------"
        "|-------------|------|\n"
        "| implement* | shared-model | 1 | 100 | 10 | 40 | 100 | 1 | 20 | 10s |\n"
        "| implement* | shared-model | 1 | 200 | 20 | 80 | 200 | 2 | 40 | 20s |\n"
        "| implement* | shared-model | 1 | 300 | 30 | 120 | 300 | 3 | 60 | 30s |\n"
        "| implement (unknown/unknown)* | shared-model | 1 | 400 | 40 | 160 | 400"
        " | 4 | 80 | 40s |\n"
        "| **Total** | | | 600 | 60 | 240 | 300 | | 120 | 1m 0s |\n"
        "| **Total (unknown/unknown)** | | | 400 | 40 | 160 | 400 | | 80 | 40s |\n\n"
        r"\* *Step used a non-Anthropic provider; caching behavior may differ.*"
    )
    expected_tables = (
        token_golden,
        TelemetryFormatter.format_efficiency_table(steps, total),
        TelemetryFormatter.format_model_table(models),
    )
    assert hook_tables == expected_tables
    assert hook_tables[0].count("| implement* |") == 3
    assert "implement (unknown/unknown)" in hook_tables[0]
    assert "unknown/unknown: shared-model" in hook_tables[2]
    assert "**Total (unknown/unknown)**" in hook_tables[1]
    assert "\n\n".join(hook_tables) == "\n\n".join(expected_tables)


@pytest.mark.parametrize(
    "schema_version,raw,expected",
    [
        pytest.param(3, 0, {"state": "unknown", "value": None}, id="legacy-zero"),
        pytest.param(4, 0, {"state": "measured_zero", "value": 0}, id="v4-numeric-zero"),
        pytest.param(
            4,
            {"state": "measured_zero", "value": 0},
            {"state": "measured_zero", "value": 0},
            id="v4-serialized-zero",
        ),
    ],
)
def test_schema_zero_measure_projection(
    tmp_path: Path, schema_version: int, raw: object, expected: dict
) -> None:
    from autoskillit.hooks.token_summary_hook import _load_sessions

    log_root = tmp_path / "logs"
    log_root.mkdir()
    kitchen_id = "schema-zero"
    _write_sessions(log_root, [{"dir_name": "s1", "kitchen_id": kitchen_id, "step_name": "plan"}])
    path = log_root / "sessions" / "s1" / "token_usage.json"
    payload = json.loads(path.read_text())
    payload.update(schema_version=schema_version, cache_read_tokens=raw)
    path.write_text(json.dumps(payload))
    result = _load_sessions(log_root, kitchen_id)
    assert next(iter(result.values()))["cache_read_tokens"] == expected


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param({"state": "measured", "value": 0}, id="measured-zero-value"),
        pytest.param({"state": "measured_zero", "value": 7}, id="measured-zero-seven"),
        pytest.param({"state": "measured", "value": -3}, id="negative-measured"),
    ],
)
def test_canonical_rejection_downgrades_malformed_measures(tmp_path: Path, raw: dict) -> None:
    from autoskillit.hooks.token_summary_hook import _load_sessions

    log_root = tmp_path / "logs"
    log_root.mkdir()
    kitchen_id = "malformed-measure"
    _write_sessions(log_root, [{"dir_name": "s1", "kitchen_id": kitchen_id, "step_name": "plan"}])
    path = log_root / "sessions" / "s1" / "token_usage.json"
    payload = json.loads(path.read_text())
    payload.update(schema_version=4, cache_read_tokens=raw)
    path.write_text(json.dumps(payload))
    result = _load_sessions(log_root, kitchen_id)
    assert next(iter(result.values()))["cache_read_tokens"] == {"state": "unknown", "value": None}


def test_shared_authority_substitution_reaches_all_rendered_tables(
    monkeypatch: pytest.MonkeyPatch, token_summary_source_corpus: dict[str, dict]
) -> None:
    from dataclasses import replace

    from autoskillit.hooks import token_summary_hook as hook

    real_aggregate = hook.aggregate_measures
    real_ratio = hook.measure_ratio
    calls = {"aggregate": 0, "ratio": 0}

    def aggregate_wrapper(*args, **kwargs):
        calls["aggregate"] += 1
        aggregate = real_aggregate(*args, **kwargs)
        fields = dict(aggregate.fields)
        fields["input_tokens"] = replace(
            fields["input_tokens"], value=hook.TokenMeasure.observed(1_234_567)
        )
        return replace(aggregate, fields=fields)

    def ratio_wrapper(*args, **kwargs):
        calls["ratio"] += 1
        ratio = real_ratio(*args, **kwargs)
        return replace(ratio, numerator_total=234, denominator_total=10, value=23.4)

    monkeypatch.setattr(hook, "aggregate_measures", aggregate_wrapper)
    monkeypatch.setattr(hook, "measure_ratio", ratio_wrapper)
    token = hook._format_table(token_summary_source_corpus)
    model = hook._format_model_table(token_summary_source_corpus)
    efficiency = hook._format_efficiency_table(token_summary_source_corpus)
    assert calls["aggregate"] and calls["ratio"]
    assert "1.2M" in token and "1.2M" in model
    assert "23.4" in efficiency


@pytest.mark.skipif(shutil.which("python3") is None, reason="python3 not on PATH")
def test_projected_shared_import_uses_bundled_measure_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.core import PluginLoadMode
    from autoskillit.execution.backends.claude import ClaudeCodeBackend
    from autoskillit.workspace import project_default_plugin_authority
    from tests.contracts._projection_helpers import session_catalog

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    authority = project_default_plugin_authority(
        cwd=tmp_path, base_branch="main", catalog=session_catalog()
    )
    with authority.acquire_launch_binding(
        backend=ClaudeCodeBackend(), load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR
    ) as binding:
        assert binding.plugin_dir is not None
        projected_hook = binding.plugin_dir / "hooks" / "token_summary_hook.py"
        projected_asset = binding.plugin_dir / "_measure_aggregation.py"
        assert projected_asset.is_file()
        script = (
            "import runpy, sys\n"
            "runpy.run_path(sys.argv[1], run_name='projected_probe')\n"
            "module = sys.modules['_measure_aggregation']\n"
            "print(module.__file__)\n"
            "print(any(name == 'autoskillit' or name.startswith('autoskillit.') "
            "for name in sys.modules))\n"
        )
        result = subprocess.run(
            [shutil.which("python3") or "python3", "-I", "-S", "-c", script, str(projected_hook)],
            capture_output=True,
            text=True,
            env=production_interpreter_env(),
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert Path(result.stdout.splitlines()[0]).resolve() == projected_asset.resolve()
        assert result.stdout.splitlines()[1] == "False"


def test_rendered_source_label_collision_keeps_pairs_separate() -> None:
    from autoskillit.hooks.token_summary_hook import (
        _format_efficiency_table,
        _format_model_table,
        _format_table,
    )

    entries = {
        backend: {
            "step_name": "plan",
            "backend": backend,
            "provider_used": provider,
            "model": "claude-test",
            "input_tokens": tokens,
            "output_tokens": tokens,
            "cache_read_tokens": tokens,
            "cache_write_tokens": tokens,
            "peak_context": tokens,
            "elapsed_seconds": 1.0,
            "loc_insertions": 10,
            "loc_deletions": 0,
        }
        for backend, provider, tokens in [("a/b", "c", 100), ("a", "b/c", 200)]
    }
    assert _format_table(entries).splitlines()[-2:] == [
        "| **Total (a/b/c)** | | | 100 | 100 | 100 | 100 | | 100 | 1s |",
        "| **Total (a/b/c)** | | | 200 | 200 | 200 | 200 | | 200 | 1s |",
    ]
    assert _format_efficiency_table(entries).splitlines()[-2:] == [
        "| **Total (a/b/c)** | **10** | 10.0 | 10.0 | 10.0 |",
        "| **Total (a/b/c)** | **10** | 20.0 | 20.0 | 20.0 |",
    ]
    assert _format_model_table(entries).splitlines()[-2:] == [
        "| a/b/c: claude-test | 1 | 100 | 100 | 100 | 100 | 1s |",
        "| a/b/c: claude-test | 1 | 200 | 200 | 200 | 200 | 1s |",
    ]
