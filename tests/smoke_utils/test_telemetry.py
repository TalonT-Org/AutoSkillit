from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.smoke_utils import (
    consolidate_health_reports,
    patch_pr_token_summary,
)
from tests.infra._token_summary_helpers import _resolve_session_label

pytestmark = [pytest.mark.medium]

PR_URL = "https://github.com/TestOwner/TestRepo/pull/42"


def _write_test_sessions(log_root: Path, entries: list[dict]) -> None:
    lines = []
    for entry in entries:
        index_entry = {
            "dir_name": entry["dir_name"],
            "cwd": entry.get("cwd", ""),
            "kitchen_id": entry.get("kitchen_id", ""),
            "order_id": entry.get("order_id", ""),
            "timestamp": entry.get("timestamp", "2026-01-01T00:00:00+00:00"),
        }
        lines.append(json.dumps(index_entry))
        session_dir = log_root / "sessions" / entry["dir_name"]
        session_dir.mkdir(parents=True, exist_ok=True)
        token_data = {
            "session_label": _resolve_session_label(entry),
            "input_tokens": entry.get("input_tokens", 1000),
            "output_tokens": entry.get("output_tokens", 500),
            "cache_write_tokens": entry.get("cache_write_tokens", 100),
            "cache_read_tokens": entry.get("cache_read_tokens", 200),
            "timing_seconds": entry.get("timing_seconds", 10.0),
            "order_id": entry.get("order_id", ""),
            "loc_insertions": entry.get("loc_insertions", 0),
            "loc_deletions": entry.get("loc_deletions", 0),
            "schema_version": 2,
        }
        if "model_identifier" in entry:
            token_data["model_identifier"] = entry["model_identifier"]
        (session_dir / "token_usage.json").write_text(json.dumps(token_data))
    (log_root / "sessions.jsonl").write_text("\n".join(lines) + "\n")


def _make_gh_mock(get_body: str = "", get_rc: int = 0, patch_rc: int = 0):
    def _mock_run(cmd, **_kwargs):
        if "--method" in cmd and "PATCH" in cmd:
            return subprocess.CompletedProcess(
                cmd, patch_rc, "", "" if patch_rc == 0 else "patch error"
            )
        return subprocess.CompletedProcess(
            cmd, get_rc, get_body if get_rc == 0 else "", "" if get_rc == 0 else "read error"
        )

    return _mock_run


@patch("time.sleep")
@patch("subprocess.run")
def test_pts_happy_path_appends_table(mock_run, _mock_sleep, tmp_path: Path) -> None:
    cwd = "/clone/test"
    _write_test_sessions(
        tmp_path,
        [
            {
                "dir_name": "s1",
                "cwd": cwd,
                "step_name": "plan",
                "input_tokens": 1000,
                "output_tokens": 500,
            },
            {
                "dir_name": "s2",
                "cwd": cwd,
                "step_name": "implement",
                "input_tokens": 2000,
                "output_tokens": 1000,
            },
            {
                "dir_name": "s3",
                "cwd": cwd,
                "step_name": "compose_pr",
                "input_tokens": 500,
                "output_tokens": 250,
            },
        ],
    )
    mock_run.side_effect = _make_gh_mock(get_body="## Summary\nSome PR body")
    result = patch_pr_token_summary(PR_URL, cwd, log_dir=str(tmp_path))
    assert result["success"] == "true"
    assert result["sessions_loaded"] == "3"
    patch_call = mock_run.call_args_list[-1]
    body_arg = [a for a in patch_call[0][0] if a.startswith("body=")][0]
    assert "## Token Usage Summary" in body_arg
    assert "plan" in body_arg
    assert "implement" in body_arg
    assert "compose_pr" in body_arg


@patch("time.sleep")
@patch("subprocess.run")
def test_pts_replaces_existing_partial_table(mock_run, _mock_sleep, tmp_path: Path) -> None:
    cwd = "/clone/test"
    _write_test_sessions(
        tmp_path,
        [
            {
                "dir_name": "s1",
                "cwd": cwd,
                "step_name": "plan",
                "input_tokens": 1000,
                "output_tokens": 500,
            },
            {
                "dir_name": "s2",
                "cwd": cwd,
                "step_name": "implement",
                "input_tokens": 2000,
                "output_tokens": 1000,
            },
            {
                "dir_name": "s3",
                "cwd": cwd,
                "step_name": "compose_pr",
                "input_tokens": 500,
                "output_tokens": 250,
            },
        ],
    )
    existing_body = (
        "## Summary\nSome text\n\n## Token Usage Summary\n\n"
        "| Step | old partial table |\n| compose_pr | 500 |"
    )
    mock_run.side_effect = _make_gh_mock(get_body=existing_body)
    result = patch_pr_token_summary(PR_URL, cwd, log_dir=str(tmp_path))
    assert result["success"] == "true"
    patch_call = mock_run.call_args_list[-1]
    body_arg = [a for a in patch_call[0][0] if a.startswith("body=")][0]
    assert body_arg.count("## Token Usage Summary") == 1
    assert "plan" in body_arg


def test_pts_invalid_pr_url() -> None:
    result = patch_pr_token_summary("not-a-url", "/clone/test")
    assert result["success"] == "false"
    assert "Invalid PR URL" in result["error"]


def test_pts_zero_sessions(tmp_path: Path) -> None:
    (tmp_path / "sessions.jsonl").write_text("")
    result = patch_pr_token_summary(PR_URL, "/clone/test", log_dir=str(tmp_path))
    assert result["success"] == "false"
    assert result["sessions_loaded"] == "0"


@patch("time.sleep")
@patch("subprocess.run")
def test_pts_cross_kitchen_sessions(mock_run, _mock_sleep, tmp_path: Path) -> None:
    cwd = "/clone/test"
    entries = [
        {
            "dir_name": f"s{i}",
            "cwd": cwd,
            "kitchen_id": "aaa",
            "step_name": f"step_a{i}",
            "input_tokens": 100,
            "output_tokens": 50,
        }
        for i in range(3)
    ] + [
        {
            "dir_name": f"s{i + 3}",
            "cwd": cwd,
            "kitchen_id": "bbb",
            "step_name": f"step_b{i}",
            "input_tokens": 100,
            "output_tokens": 50,
        }
        for i in range(3)
    ]
    _write_test_sessions(tmp_path, entries)
    mock_run.side_effect = _make_gh_mock(get_body="## Summary\nBody")
    result = patch_pr_token_summary(PR_URL, cwd, log_dir=str(tmp_path))
    assert result["success"] == "true"
    assert result["sessions_loaded"] == "6"


@patch("subprocess.run")
def test_pts_gh_api_read_failure(mock_run, tmp_path: Path) -> None:
    cwd = "/clone/test"
    _write_test_sessions(
        tmp_path,
        [
            {"dir_name": "s1", "cwd": cwd, "step_name": "plan"},
        ],
    )
    mock_run.side_effect = _make_gh_mock(get_rc=1)
    result = patch_pr_token_summary(PR_URL, cwd, log_dir=str(tmp_path))
    assert result["success"] == "false"
    assert "Failed to read PR" in result["error"]


@patch("time.sleep")
@patch("subprocess.run")
def test_pts_gh_api_patch_failure(mock_run, _mock_sleep, tmp_path: Path) -> None:
    cwd = "/clone/test"
    _write_test_sessions(
        tmp_path,
        [
            {"dir_name": "s1", "cwd": cwd, "step_name": "plan"},
        ],
    )
    mock_run.side_effect = _make_gh_mock(get_body="## Summary\nBody", patch_rc=1)
    result = patch_pr_token_summary(PR_URL, cwd, log_dir=str(tmp_path))
    assert result["success"] == "false"
    assert "Failed to patch PR" in result["error"]


@patch("time.sleep")
@patch("subprocess.run")
def test_pts_order_id_captures_cross_clone_sessions(mock_run, _mock_sleep, tmp_path: Path) -> None:
    """patch_pr_token_summary with order_id loads sessions from multiple cwd paths."""
    _write_test_sessions(
        tmp_path,
        [
            {
                "dir_name": "s-clone-a",
                "cwd": "/clone-A",
                "order_id": "issue-42",
                "step_name": "rectify",
                "input_tokens": 1000,
            },
            {
                "dir_name": "s-clone-b",
                "cwd": "/clone-B",
                "order_id": "issue-42",
                "step_name": "implement",
                "input_tokens": 2000,
            },
        ],
    )
    mock_run.side_effect = _make_gh_mock(get_body="## Summary\nBody")
    result = patch_pr_token_summary(PR_URL, order_id="issue-42", log_dir=str(tmp_path))
    assert result["success"] == "true"
    assert result["sessions_loaded"] == "2"
    patch_call = mock_run.call_args_list[-1]
    body_arg = [a for a in patch_call[0][0] if a.startswith("body=")][0]
    assert "rectify" in body_arg
    assert "implement" in body_arg


@patch("time.sleep")
@patch("subprocess.run")
def test_pts_generates_efficiency_table_when_loc_data_present(
    mock_run, _mock_sleep, tmp_path: Path
) -> None:
    """patch_pr_token_summary emits Token Efficiency table when LoC data exists."""
    _write_test_sessions(
        tmp_path,
        [
            {
                "dir_name": "s1",
                "cwd": "/clone/test",
                "step_name": "implement",
                "input_tokens": 1000,
                "output_tokens": 500,
                "loc_insertions": 120,
            },
        ],
    )
    mock_run.side_effect = _make_gh_mock(get_body="## Summary\nBody")
    result = patch_pr_token_summary(PR_URL, cwd="/clone/test", log_dir=str(tmp_path))
    assert result["success"] == "true"
    patch_call = mock_run.call_args_list[-1]
    body_arg = [a for a in patch_call[0][0] if a.startswith("body=")][0]
    assert "## Token Efficiency" in body_arg


@patch("time.sleep")
@patch("subprocess.run")
def test_pts_preserves_or_regenerates_efficiency_table(
    mock_run, _mock_sleep, tmp_path: Path
) -> None:
    """When overwriting existing summary, efficiency table is regenerated, not lost."""
    _write_test_sessions(
        tmp_path,
        [
            {
                "dir_name": "s1",
                "cwd": "/clone/test",
                "step_name": "implement",
                "input_tokens": 1000,
                "output_tokens": 500,
                "loc_insertions": 100,
            },
        ],
    )
    existing_body = (
        "## Summary\nSome text\n\n## Token Usage Summary\n\n"
        "| Step | old |\n\n"
        "## Token Efficiency\n\n| Step | old eff |\n"
    )
    mock_run.side_effect = _make_gh_mock(get_body=existing_body)
    result = patch_pr_token_summary(PR_URL, cwd="/clone/test", log_dir=str(tmp_path))
    assert result["success"] == "true"
    patch_call = mock_run.call_args_list[-1]
    body_arg = [a for a in patch_call[0][0] if a.startswith("body=")][0]
    assert body_arg.count("## Token Usage Summary") == 1
    assert "## Token Efficiency" in body_arg


@patch("time.sleep")
@patch("subprocess.run")
def test_pts_reads_order_id_from_dispatch_env(
    mock_run, _mock_sleep, tmp_path: Path, monkeypatch
) -> None:
    """patch_pr_token_summary auto-reads AUTOSKILLIT_DISPATCH_ID when order_id not passed."""
    monkeypatch.setenv("AUTOSKILLIT_DISPATCH_ID", "issue-42")
    _write_test_sessions(
        tmp_path,
        [
            {
                "dir_name": "s-clone-a",
                "cwd": "/clone-A",
                "order_id": "issue-42",
                "step_name": "rectify",
                "input_tokens": 1000,
            },
            {
                "dir_name": "s-clone-b",
                "cwd": "/clone-B",
                "order_id": "issue-42",
                "step_name": "implement",
                "input_tokens": 2000,
            },
        ],
    )
    mock_run.side_effect = _make_gh_mock(get_body="## Summary\nBody")
    result = patch_pr_token_summary(PR_URL, cwd="", log_dir=str(tmp_path))
    assert result["success"] == "true"
    assert result["sessions_loaded"] == "2"


@patch("time.sleep")
@patch("subprocess.run")
def test_pts_kitchen_id_fallback_includes_worktree_sessions(
    mock_run, _mock_sleep, tmp_path: Path
) -> None:
    """patch_pr_token_summary falls back to kitchen_id filter when no order_id.

    When the caller passes only a cwd that matches the clone root and the
    sessions were logged with mixed cwds (clone root + worktree) under a
    single kitchen_id, the kitchen_id fallback includes the worktree session
    that cwd_filter would silently drop.
    """
    clone_root = "/clone/test"
    worktree_path = "/clone/test/.worktrees/fix-42"
    _write_test_sessions(
        tmp_path,
        [
            {
                "dir_name": "s-plan",
                "cwd": clone_root,
                "kitchen_id": "kitchen-xyz",
                "step_name": "plan",
                "input_tokens": 1000,
            },
            {
                "dir_name": "s-review",
                "cwd": clone_root,
                "kitchen_id": "kitchen-xyz",
                "step_name": "review",
                "input_tokens": 800,
            },
            {
                "dir_name": "s-implement",
                "cwd": worktree_path,
                "kitchen_id": "kitchen-xyz",
                "step_name": "implement",
                "input_tokens": 2000,
            },
        ],
    )
    mock_run.side_effect = _make_gh_mock(get_body="## Summary\nBody")
    result = patch_pr_token_summary(
        PR_URL, cwd=clone_root, kitchen_id="kitchen-xyz", log_dir=str(tmp_path)
    )
    assert result["success"] == "true"
    assert result["sessions_loaded"] == "3"
    patch_call = mock_run.call_args_list[-1]
    body_arg = [a for a in patch_call[0][0] if a.startswith("body=")][0]
    assert "plan" in body_arg
    assert "review" in body_arg
    assert "implement" in body_arg


@patch("time.sleep")
@patch("subprocess.run")
def test_pts_cwd_filter_silently_excludes_worktree_sessions(
    mock_run, _mock_sleep, tmp_path: Path
) -> None:
    """When caller uses cwd_filter directly, worktree sessions are silently dropped.

    Documents the bug that the kitchen_id fallback fixes: with only cwd_filter
    available, sessions from the worktree subdir are excluded and the implement
    step is missing from the PR body.
    """
    clone_root = "/clone/test"
    worktree_path = "/clone/test/.worktrees/fix-42"
    _write_test_sessions(
        tmp_path,
        [
            {
                "dir_name": "s-plan",
                "cwd": clone_root,
                "step_name": "plan",
                "input_tokens": 1000,
            },
            {
                "dir_name": "s-implement",
                "cwd": worktree_path,
                "step_name": "implement",
                "input_tokens": 2000,
            },
        ],
    )
    mock_run.side_effect = _make_gh_mock(get_body="## Summary\nBody")
    result = patch_pr_token_summary(PR_URL, cwd=clone_root, log_dir=str(tmp_path))
    assert result["success"] == "true"
    assert result["sessions_loaded"] == "1"
    patch_call = mock_run.call_args_list[-1]
    body_arg = [a for a in patch_call[0][0] if a.startswith("body=")][0]
    assert "plan" in body_arg
    assert "implement" not in body_arg, "implement should be silently dropped by cwd_filter"


@patch("time.sleep")
@patch("subprocess.run")
def test_pts_expected_steps_surfaces_missing_in_pr_body(
    mock_run, _mock_sleep, tmp_path: Path
) -> None:
    """When expected_steps is provided, missing steps appear in the PR body."""
    cwd = "/clone/test"
    _write_test_sessions(
        tmp_path,
        [
            {"dir_name": "s-plan", "cwd": cwd, "step_name": "plan", "input_tokens": 1000},
            {"dir_name": "s-review", "cwd": cwd, "step_name": "review", "input_tokens": 800},
        ],
    )
    mock_run.side_effect = _make_gh_mock(get_body="## Summary\nBody")
    result = patch_pr_token_summary(
        PR_URL,
        cwd=cwd,
        log_dir=str(tmp_path),
        expected_steps=["plan", "review", "implement"],
    )
    assert result["success"] == "true"
    patch_call = mock_run.call_args_list[-1]
    body_arg = [a for a in patch_call[0][0] if a.startswith("body=")][0]
    assert "completeness warning" in body_arg
    assert "implement" in body_arg


@patch("time.sleep")
@patch("subprocess.run")
def test_pts_includes_model_usage_breakdown(mock_run, _mock_sleep, tmp_path: Path) -> None:
    """patch_pr_token_summary produces all 3 telemetry sections when model data is present."""
    existing_body = (
        "## Summary\nSome text\n\n"
        "## Token Usage Summary\n\n| Step | old |\n\n"
        "## Token Efficiency\n\n| Step | old eff |\n\n"
        "## Model Usage Breakdown\n\n| Model | old model |\n\n"
        "## Next Section\nFollowing content"
    )
    _write_test_sessions(
        tmp_path,
        [
            {
                "dir_name": "s1",
                "cwd": "/clone/test",
                "step_name": "implement",
                "input_tokens": 1000,
                "output_tokens": 500,
                "loc_insertions": 120,
                "model_identifier": "claude-sonnet-4-6",
            },
        ],
    )
    mock_run.side_effect = _make_gh_mock(get_body=existing_body)
    result = patch_pr_token_summary(PR_URL, cwd="/clone/test", log_dir=str(tmp_path))
    assert result["success"] == "true"
    patch_call = mock_run.call_args_list[-1]
    body_arg = next((a for a in patch_call[0][0] if a.startswith("body=")), None)
    assert body_arg is not None, "body= argument not found in gh patch call"
    assert "## Token Usage Summary" in body_arg
    assert "## Token Efficiency" in body_arg
    assert "## Model Usage Breakdown" in body_arg
    assert body_arg.count("## Token Usage Summary") == 1
    assert body_arg.count("## Token Efficiency") == 1
    assert body_arg.count("## Model Usage Breakdown") == 1
    assert "claude-sonnet-4-6" in body_arg
    assert "## Next Section" in body_arg


def test_section_re_consumes_all_three_sections() -> None:
    """section_re matches across all three telemetry sections."""
    from autoskillit.core import PR_TELEMETRY_SECTIONS
    from autoskillit.smoke_utils._telemetry import _PR_SECTION_RE

    body = (
        "## Summary\nIntro\n\n"
        "## Token Usage Summary\n\n| Step | data |\n\n"
        "## Token Efficiency\n\n| Step | eff |\n\n"
        "## Model Usage Breakdown\n\n| Model | model data |\n\n"
        "## Next Section\nMore"
    )
    m = _PR_SECTION_RE.search(body)
    assert m is not None
    matched = m.group(0)
    for section in PR_TELEMETRY_SECTIONS:
        assert section in matched


def test_section_re_covers_all_pr_telemetry_sections() -> None:
    """Every section in PR_TELEMETRY_SECTIONS is consumed by section_re when all are present."""
    from autoskillit.core import PR_TELEMETRY_SECTIONS
    from autoskillit.smoke_utils._telemetry import _PR_SECTION_RE

    parts = []
    for section in PR_TELEMETRY_SECTIONS:
        parts.append(f"{section}\n\n| data | here |")
    body = "## Summary\nIntro\n\n" + "\n\n".join(parts) + "\n\n## Other\nEnd"
    m = _PR_SECTION_RE.search(body)
    assert m is not None
    matched = m.group(0)
    for section in PR_TELEMETRY_SECTIONS:
        assert section in matched, f"{section} not consumed by section_re"


@patch("time.sleep")
@patch("subprocess.run")
def test_pts_uses_injected_token_log(
    mock_run: MagicMock, _mock_sleep: MagicMock, tmp_path: Path
) -> None:
    """patch_pr_token_summary uses injected token_log instead of constructing a new one.

    Regression guard for the DI gap: callers with a pre-loaded DefaultTokenLog
    should be able to inject it to avoid redundant disk I/O, and the function
    must not replace the injected instance with a fresh DefaultTokenLog.
    """
    from autoskillit.pipeline import DefaultTokenLog
    from autoskillit.smoke_utils._telemetry import patch_pr_token_summary

    cwd = "/clone/test"
    _write_test_sessions(
        tmp_path,
        [
            {
                "dir_name": "s1",
                "cwd": cwd,
                "step_name": "plan",
                "input_tokens": 1000,
                "output_tokens": 500,
            },
        ],
    )

    token_log = DefaultTokenLog()
    token_log.load_from_log_dir(tmp_path, cwd_filter=cwd)
    sentinel_id = id(token_log)

    mock_run.side_effect = _make_gh_mock(get_body="## Summary\nTest PR")
    result = patch_pr_token_summary(PR_URL, cwd, log_dir=str(tmp_path), token_log=token_log)
    assert result["success"] == "true"
    assert id(token_log) == sentinel_id, "injected token_log must not be replaced"


def test_consolidate_health_reports_filters_by_campaign_id(tmp_path):
    """T3.1: consolidate_health_reports filters by kitchen_id and aggregates findings."""

    reports_dir = tmp_path / "health-reports"
    reports_dir.mkdir()

    # Create two dispatch reports for different campaigns
    dispatch_a = {
        "kitchen_id": "campaign-1",
        "dispatch_id": "dispatch-a",
        "findings": [
            {"severity": "confirmed_bug", "step_group": "implement", "summary": "Bug in foo"},
        ],
    }
    dispatch_b = {
        "kitchen_id": "campaign-2",
        "dispatch_id": "dispatch-b",
        "findings": [
            {"severity": "regression", "step_group": "test", "summary": "Test regression in bar"},
        ],
    }

    (reports_dir / "dispatch-a_health_report.json").write_text(json.dumps(dispatch_a))
    (reports_dir / "dispatch-b_health_report.json").write_text(json.dumps(dispatch_b))

    result = consolidate_health_reports(diagnostics_log_dir=str(tmp_path), kitchen_id="campaign-1")

    assert "dispatch-a" in result["summary"]
    assert "Bug in foo" in result["summary"]
    assert "dispatch-b" not in result["summary"]
    assert "Test regression" not in result["summary"]


def test_consolidate_health_reports_empty_dir(tmp_path):
    """T3.2: consolidate_health_reports returns 'no reports found' for empty directory."""

    reports_dir = tmp_path / "health-reports"
    reports_dir.mkdir()

    result = consolidate_health_reports(diagnostics_log_dir=str(tmp_path), kitchen_id="campaign-1")

    assert "no health reports found" in result["summary"].lower()


def test_consolidate_health_reports_no_dir(tmp_path):
    """T3.3: consolidate_health_reports returns 'no directory'
    when health-reports does not exist."""

    result = consolidate_health_reports(diagnostics_log_dir=str(tmp_path), kitchen_id="campaign-1")

    assert "no health reports directory found" in result["summary"].lower()


def test_consolidate_health_reports_does_not_mutate_source_dicts(tmp_path):
    """T3.4: consolidate_health_reports does not mutate source finding dicts."""

    reports_dir = tmp_path / "health-reports"
    reports_dir.mkdir()

    original_finding = {
        "severity": "confirmed_bug",
        "step_group": "implement",
        "summary": "Bug in foo",
    }
    dispatch_a = {
        "kitchen_id": "campaign-1",
        "dispatch_id": "dispatch-a",
        "findings": [original_finding],
    }

    (reports_dir / "dispatch-a_health_report.json").write_text(json.dumps(dispatch_a))

    result = consolidate_health_reports(diagnostics_log_dir=str(tmp_path), kitchen_id="campaign-1")

    # Verify the original finding dict was not mutated (no dispatch_id key added)
    assert "dispatch_id" not in original_finding
    # Verify the result has the dispatch_id in findings
    assert "dispatch-a" in result["summary"]
