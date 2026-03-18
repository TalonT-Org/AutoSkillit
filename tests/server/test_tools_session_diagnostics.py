"""Tests for session diagnostics helpers in tools_integrations."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from autoskillit.server.tools_integrations import (
    _format_diagnostics_section,
    _read_session_diagnostics,
    report_bug,
)

from autoskillit.core import SkillResult
from autoskillit.core.types import RetryReason

# ---------------------------------------------------------------------------
# _read_session_diagnostics unit tests
# ---------------------------------------------------------------------------


def test_read_session_diagnostics_returns_none_for_empty_session_id(tmp_path):
    """Empty session_id → None, no filesystem access."""
    result = _read_session_diagnostics("", str(tmp_path))
    assert result is None


def test_read_session_diagnostics_returns_none_for_no_session_prefix(tmp_path):
    """no_session_* session IDs → None (not meaningful diagnostics)."""
    assert _read_session_diagnostics("no_session_2026-01-01T00-00-00", str(tmp_path)) is None


def test_read_session_diagnostics_returns_none_for_crashed_prefix(tmp_path):
    """crashed_* session IDs → None (not meaningful diagnostics)."""
    assert _read_session_diagnostics("crashed_12345_2026-01-01T00-00-00", str(tmp_path)) is None


def test_read_session_diagnostics_returns_none_for_path_traversal_session_id(tmp_path):
    """Path-traversal session IDs blocked by _SAFE_SESSION_ID_RE → None."""
    assert _read_session_diagnostics("../../../etc/passwd", str(tmp_path)) is None
    assert _read_session_diagnostics("..%2F..%2Fetc%2Fpasswd", str(tmp_path)) is None
    assert _read_session_diagnostics("abc/../../etc", str(tmp_path)) is None


def test_read_session_diagnostics_returns_none_when_directory_missing(tmp_path):
    """Valid session_id but no directory on disk → None."""
    result = _read_session_diagnostics("abc-123", str(tmp_path))
    assert result is None


def test_read_session_diagnostics_reads_summary_json(tmp_path):
    """Reads and returns summary.json contents."""
    session_id = "test-session-abc"
    session_dir = tmp_path / "sessions" / session_id
    session_dir.mkdir(parents=True)
    summary = {
        "session_id": session_id,
        "duration_seconds": 42.0,
        "peak_rss_kb": 1024,
        "peak_oom_score": 5,
        "anomaly_count": 0,
        "termination_reason": "NATURAL_EXIT",
        "exit_code": 0,
        "claude_code_log": "/path/to/log.jsonl",
    }
    (session_dir / "summary.json").write_text(json.dumps(summary))

    result = _read_session_diagnostics(session_id, str(tmp_path))
    assert result is not None
    assert result["summary"]["duration_seconds"] == 42.0
    assert result["session_id"] == session_id
    assert result["session_dir"] == str(session_dir)


def test_read_session_diagnostics_reads_anomalies_jsonl(tmp_path):
    """Reads all records from anomalies.jsonl."""
    session_id = "test-session-def"
    session_dir = tmp_path / "sessions" / session_id
    session_dir.mkdir(parents=True)
    (session_dir / "summary.json").write_text(json.dumps({"session_id": session_id}))
    anomalies = [
        {"kind": "oom_spike", "severity": "warning", "detail": {"delta": 250}},
        {"kind": "rss_growth", "severity": "warning", "detail": {"ratio": 2.5}},
    ]
    (session_dir / "anomalies.jsonl").write_text("\n".join(json.dumps(a) for a in anomalies))

    result = _read_session_diagnostics(session_id, str(tmp_path))
    assert len(result["anomalies"]) == 2
    assert result["anomalies"][0]["kind"] == "oom_spike"


def test_read_session_diagnostics_reads_proc_trace_tail_10(tmp_path):
    """Reads only the last 10 lines of proc_trace.jsonl."""
    session_id = "test-session-ghi"
    session_dir = tmp_path / "sessions" / session_id
    session_dir.mkdir(parents=True)
    (session_dir / "summary.json").write_text(json.dumps({"session_id": session_id}))
    snapshots = [{"seq": i, "vm_rss_kb": i * 100} for i in range(15)]
    (session_dir / "proc_trace.jsonl").write_text("\n".join(json.dumps(s) for s in snapshots))

    result = _read_session_diagnostics(session_id, str(tmp_path))
    assert len(result["proc_trace_tail"]) == 10
    assert result["proc_trace_tail"][0]["seq"] == 5  # starts from seq=5 (last 10 of 15)


def test_read_session_diagnostics_handles_missing_optional_files(tmp_path):
    """Returns empty lists when anomalies.jsonl and proc_trace.jsonl are absent."""
    session_id = "test-session-jkl"
    session_dir = tmp_path / "sessions" / session_id
    session_dir.mkdir(parents=True)
    (session_dir / "summary.json").write_text(json.dumps({"session_id": session_id}))

    result = _read_session_diagnostics(session_id, str(tmp_path))
    assert result["anomalies"] == []
    assert result["proc_trace_tail"] == []


# ---------------------------------------------------------------------------
# _format_diagnostics_section unit tests
# ---------------------------------------------------------------------------


def test_format_diagnostics_section_full_includes_metrics_table():
    """Full format includes the metrics table."""
    diag = {
        "session_id": "abc-123",
        "session_dir": "/logs/sessions/abc-123",
        "summary": {
            "session_id": "abc-123",
            "duration_seconds": 30.5,
            "peak_rss_kb": 2048,
            "peak_oom_score": 10,
            "anomaly_count": 1,
            "termination_reason": "NATURAL_EXIT",
            "exit_code": 0,
            "claude_code_log": "/logs/claude.jsonl",
        },
        "anomalies": [{"kind": "oom_spike", "severity": "warning", "detail": {"delta": 210}}],
        "proc_trace_tail": [],
    }
    output = _format_diagnostics_section(diag, condensed=False)
    assert "## Session Diagnostics" in output
    assert "Session ID" in output
    assert "abc-123" in output
    assert "30.5s" in output
    assert "2048 KB" in output


def test_format_diagnostics_section_full_includes_anomalies_details_block():
    """Full format includes <details> block when anomalies present."""
    diag = {
        "session_id": "abc-123",
        "session_dir": "/logs/sessions/abc-123",
        "summary": {"session_id": "abc-123", "anomaly_count": 1},
        "anomalies": [{"kind": "oom_spike", "severity": "warning", "detail": {}}],
        "proc_trace_tail": [],
    }
    output = _format_diagnostics_section(diag, condensed=False)
    assert "<details>" in output
    assert "Anomalies (1)" in output


def test_format_diagnostics_section_full_includes_proc_trace_block():
    """Full format includes <details> block for proc trace when snapshots present."""
    diag = {
        "session_id": "abc-123",
        "session_dir": "/logs/sessions/abc-123",
        "summary": {"session_id": "abc-123"},
        "anomalies": [],
        "proc_trace_tail": [{"seq": 0, "vm_rss_kb": 100}],
    }
    output = _format_diagnostics_section(diag, condensed=False)
    assert "Process Trace" in output
    assert "```json" in output


def test_format_diagnostics_section_full_omits_blocks_when_empty():
    """Full format omits <details> blocks when no anomalies and no proc trace."""
    diag = {
        "session_id": "abc-123",
        "session_dir": "/logs/sessions/abc-123",
        "summary": {"session_id": "abc-123", "anomaly_count": 0},
        "anomalies": [],
        "proc_trace_tail": [],
    }
    output = _format_diagnostics_section(diag, condensed=False)
    assert "<details>" not in output


def test_format_diagnostics_section_full_includes_local_paths():
    """Full format includes local path links."""
    diag = {
        "session_id": "abc-123",
        "session_dir": "/logs/sessions/abc-123",
        "summary": {"session_id": "abc-123", "claude_code_log": "/claude/log.jsonl"},
        "anomalies": [],
        "proc_trace_tail": [],
    }
    output = _format_diagnostics_section(diag, condensed=False)
    assert "/logs/sessions/abc-123" in output
    assert "/claude/log.jsonl" in output


def test_format_diagnostics_section_condensed_has_metrics_only():
    """Condensed format has metrics table but no <details> blocks or paths."""
    diag = {
        "session_id": "abc-123",
        "session_dir": "/logs/sessions/abc-123",
        "summary": {
            "session_id": "abc-123",
            "duration_seconds": 5.0,
            "peak_rss_kb": 512,
            "peak_oom_score": 2,
            "anomaly_count": 0,
            "termination_reason": "NATURAL_EXIT",
            "exit_code": 0,
            "claude_code_log": "/claude/log.jsonl",
        },
        "anomalies": [{"kind": "oom_spike", "severity": "warning", "detail": {}}],
        "proc_trace_tail": [{"seq": 0}],
    }
    output = _format_diagnostics_section(diag, condensed=True)
    assert "## Session Diagnostics" in output
    assert "<details>" not in output
    assert "Local paths" not in output


# ---------------------------------------------------------------------------
# Integration test helpers
# ---------------------------------------------------------------------------


def _make_mock_executor(success: bool, result: str, session_id: str) -> MagicMock:
    """Return a mock HeadlessExecutor whose run() returns a SkillResult."""
    skill_result = SkillResult(
        success=success,
        result=result,
        session_id=session_id,
        subtype="success" if success else "error",
        is_error=not success,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
    )
    executor = MagicMock()
    executor.run = AsyncMock(return_value=skill_result)
    return executor


def _make_mock_github(search_total: int, existing_body: str = "") -> MagicMock:
    """Return a mock GitHubFetcher for issue search + create/comment."""
    client = MagicMock()
    client.has_token = True
    items = (
        [
            {
                "number": 1,
                "html_url": "https://github.com/o/r/issues/1",
                "body": existing_body,
            }
        ]
        if search_total > 0
        else []
    )
    client.search_issues = AsyncMock(
        return_value={"success": True, "total_count": search_total, "items": items}
    )
    client.create_issue = AsyncMock(
        return_value={"success": True, "url": "https://github.com/o/r/issues/99"}
    )
    client.add_comment = AsyncMock(return_value={"success": True})
    return client


def _make_session_dir(
    tmp_path: Path,
    session_id: str,
    summary_extra: dict | None = None,
    anomalies: list | None = None,
    proc_trace: list | None = None,
) -> Path:
    """Helper: create a fake session log directory."""
    session_dir = tmp_path / "session_logs" / "sessions" / session_id
    session_dir.mkdir(parents=True)
    summary: dict = {
        "session_id": session_id,
        "duration_seconds": 10.0,
        "peak_rss_kb": 1024,
        "peak_oom_score": 5,
        "anomaly_count": len(anomalies or []),
        "termination_reason": "NATURAL_EXIT",
        "exit_code": 0,
        "claude_code_log": None,
    }
    summary.update(summary_extra or {})
    (session_dir / "summary.json").write_text(json.dumps(summary))
    if anomalies:
        (session_dir / "anomalies.jsonl").write_text("\n".join(json.dumps(a) for a in anomalies))
    if proc_trace:
        (session_dir / "proc_trace.jsonl").write_text("\n".join(json.dumps(s) for s in proc_trace))
    return session_dir


# ---------------------------------------------------------------------------
# Integration tests: full report_bug flow with diagnostics
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_report_bug_includes_diagnostics_in_new_issue_body(tool_ctx, tmp_path):
    """New issue body includes the Session Diagnostics section when diagnostics are available."""
    session_id = "diag-session-001"
    _make_session_dir(tmp_path, session_id)

    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = True
    tool_ctx.config.github.default_repo = "owner/repo"
    tool_ctx.config.linux_tracing.log_dir = str(tmp_path / "session_logs")

    tool_ctx.executor = _make_mock_executor(
        success=True,
        result="---bug-fingerprint---\nfp-001\n---/bug-fingerprint---\nReport text.",
        session_id=session_id,
    )
    github_mock = _make_mock_github(search_total=0)
    tool_ctx.github_client = github_mock

    result = json.loads(
        await report_bug(error_context="Test error", cwd=str(tmp_path), severity="blocking")
    )

    assert result["success"] is True
    _args = github_mock.create_issue.call_args
    call_body = _args.kwargs.get("body", _args.args[3])
    assert "## Session Diagnostics" in call_body
    assert session_id in call_body


@pytest.mark.anyio
async def test_report_bug_includes_condensed_diagnostics_in_duplicate_comment(tool_ctx, tmp_path):
    """Duplicate comment includes condensed metrics but no <details> blocks."""
    session_id = "diag-session-002"
    _make_session_dir(
        tmp_path,
        session_id,
        anomalies=[{"kind": "oom_spike", "severity": "warning", "detail": {}}],
    )

    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = True
    tool_ctx.config.github.default_repo = "owner/repo"
    tool_ctx.config.linux_tracing.log_dir = str(tmp_path / "session_logs")

    tool_ctx.executor = _make_mock_executor(
        success=True,
        result="---bug-fingerprint---\nfp-002\n---/bug-fingerprint---\nReport text.",
        session_id=session_id,
    )
    github_mock = _make_mock_github(search_total=1, existing_body="different error")
    tool_ctx.github_client = github_mock

    await report_bug(error_context="Test error", cwd=str(tmp_path), severity="blocking")

    _args = github_mock.add_comment.call_args
    comment_body = _args.kwargs.get("body", _args.args[3])
    assert "Session Diagnostics" in comment_body
    assert "<details>" not in comment_body  # condensed — no details blocks


@pytest.mark.anyio
async def test_report_bug_proceeds_without_diagnostics_when_session_dir_missing(
    tool_ctx, tmp_path
):
    """GitHub issue is still filed even when no session diagnostics directory exists."""
    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = True
    tool_ctx.config.github.default_repo = "owner/repo"
    tool_ctx.config.linux_tracing.log_dir = str(tmp_path / "session_logs")

    tool_ctx.executor = _make_mock_executor(
        success=True,
        result="---bug-fingerprint---\nfp-003\n---/bug-fingerprint---\nReport text.",
        session_id="nonexistent-session-id",  # no directory on disk
    )
    github_mock = _make_mock_github(search_total=0)
    tool_ctx.github_client = github_mock

    result = json.loads(
        await report_bug(error_context="Test error", cwd=str(tmp_path), severity="blocking")
    )

    assert result["success"] is True
    assert github_mock.create_issue.called
    _args = github_mock.create_issue.call_args
    call_body = _args.kwargs.get("body", _args.args[3])
    assert "## Session Diagnostics" not in call_body  # graceful skip


@pytest.mark.anyio
async def test_report_bug_skips_diagnostics_for_fallback_session_id(tool_ctx, tmp_path):
    """Fallback session IDs (no_session_*) never trigger diagnostics read."""
    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = True
    tool_ctx.config.github.default_repo = "owner/repo"
    tool_ctx.config.linux_tracing.log_dir = str(tmp_path / "session_logs")

    tool_ctx.executor = _make_mock_executor(
        success=True,
        result="---bug-fingerprint---\nfp-004\n---/bug-fingerprint---\nReport.",
        session_id="no_session_2026-01-01T00-00-00",
    )
    github_mock = _make_mock_github(search_total=0)
    tool_ctx.github_client = github_mock

    result = json.loads(
        await report_bug(error_context="Test error", cwd=str(tmp_path), severity="blocking")
    )

    assert result["success"] is True
    _args = github_mock.create_issue.call_args
    call_body = _args.kwargs.get("body", _args.args[3])
    assert "## Session Diagnostics" not in call_body
