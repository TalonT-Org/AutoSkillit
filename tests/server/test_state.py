"""Tests for server/_state.py: server initialization."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_mock_ctx(tmp_path: Path) -> MagicMock:
    """Return a minimal mock ToolContext for _initialize tests."""
    ctx = MagicMock()
    ctx.plugin_dir = None
    # Provide a minimal linux_tracing config stub
    tracing_cfg = MagicMock()
    tracing_cfg.tmpfs_path = str(tmp_path / "tmpfs")
    tracing_cfg.log_dir = str(tmp_path / "logs")
    ctx.config.linux_tracing = tracing_cfg
    return ctx


# T3a
def test_initialize_runs_without_error(tmp_path, monkeypatch):
    """Server _initialize() completes without raising."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx(tmp_path)

    with patch("autoskillit.execution.recover_crashed_sessions", return_value=0):
        from autoskillit.server._state import _initialize

        _initialize(mock_ctx)  # Should not raise


def test_initialize_does_not_load_token_log(tmp_path):
    """
    _initialize() must not load any token log entries from disk.
    Token logs are per-pipeline live accumulators — startup recovery
    would contaminate them with cross-pipeline data.
    """
    from autoskillit.pipeline.audit import DefaultAuditLog
    from autoskillit.pipeline.timings import DefaultTimingLog
    from autoskillit.pipeline.tokens import DefaultTokenLog
    from autoskillit.server._state import _initialize

    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    # Write a session from a DIFFERENT pipeline cwd.
    # Use a current timestamp so it falls within _initialize's 24-hour recovery window —
    # otherwise the since= filter excludes it and the test passes trivially.
    other_cwd = str(tmp_path / "other-pipeline")
    now_ts = datetime.now(tz=UTC).isoformat()
    session_dir = log_dir / "sessions" / "sess-other"
    session_dir.mkdir(parents=True)
    (session_dir / "token_usage.json").write_text(
        json.dumps(
            {
                "step_name": "implement",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 30.0,
            }
        )
    )
    (session_dir / "step_timing.json").write_text(
        json.dumps({"step_name": "implement", "total_seconds": 30.0})
    )
    with (log_dir / "sessions.jsonl").open("a") as f:
        f.write(
            json.dumps({"dir_name": "sess-other", "timestamp": now_ts, "cwd": other_cwd}) + "\n"
        )

    token_log = DefaultTokenLog()
    timing_log = DefaultTimingLog()
    audit_log = DefaultAuditLog()
    # Use plain MagicMock (no spec) so nested config attrs are freely settable
    mock_ctx = MagicMock()
    mock_ctx.token_log = token_log
    mock_ctx.timing_log = timing_log
    mock_ctx.audit = audit_log
    mock_ctx.config.subsets.disabled = []
    mock_ctx.config.linux_tracing.log_dir = str(log_dir)
    mock_ctx.config.linux_tracing.tmpfs_path = str(tmp_path / "tmpfs")
    mock_ctx.session_skill_manager = None

    with patch("autoskillit.execution.recover_crashed_sessions", return_value=0):
        _initialize(mock_ctx)

    # Token and timing logs must remain empty — no startup recovery
    assert token_log.get_report() == [], (
        "_initialize() must not populate DefaultTokenLog from disk; "
        "token telemetry is per-pipeline only"
    )
    assert timing_log.get_report() == [], (
        "_initialize() must not populate DefaultTimingLog from disk; "
        "timing telemetry is per-pipeline only"
    )
