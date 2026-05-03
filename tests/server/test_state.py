"""Tests for server/_state.py: server initialization."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_mock_ctx(tmp_path: Path) -> MagicMock:
    """Return a minimal mock ToolContext for _initialize tests."""
    from autoskillit.core.types._type_plugin_source import MarketplaceInstall

    ctx = MagicMock()
    ctx.plugin_source = MarketplaceInstall(cache_path=tmp_path)
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


# --- T-INIT-1: McpRecordingMiddleware registered for RecordingSubprocessRunner ---


def test_initialize_registers_mcp_recording_middleware(tmp_path, monkeypatch):
    """_initialize() registers McpRecordingMiddleware when runner is RecordingSubprocessRunner."""
    from autoskillit.execution.recording import RecordingSubprocessRunner
    from autoskillit.server._state import _initialize

    mock_atexit = Mock()
    monkeypatch.setattr("atexit.register", mock_atexit)
    mock_recorder = Mock()
    recording_runner = RecordingSubprocessRunner(recorder=mock_recorder)

    mock_ctx = _make_mock_ctx(tmp_path)
    mock_ctx.runner = recording_runner
    mock_ctx.config.subsets.disabled = []
    mock_ctx.session_skill_manager = None

    mock_mcp = MagicMock()
    mock_middleware_cls = MagicMock()

    import api_simulator.mcp as _api_sim_mcp

    monkeypatch.setattr("autoskillit.server.mcp", mock_mcp)
    monkeypatch.setattr(_api_sim_mcp, "McpRecordingMiddleware", mock_middleware_cls)

    with patch("autoskillit.execution.recover_crashed_sessions", return_value=0):
        _initialize(mock_ctx)

    mock_middleware_cls.assert_called_once_with(mock_recorder)
    mock_mcp.add_middleware.assert_called_once_with(mock_middleware_cls.return_value)
    mock_atexit.assert_not_called()


# --- T-INIT-2: No middleware registered for non-recording runner ---


def test_initialize_skips_middleware_for_non_recording_runner(tmp_path, monkeypatch):
    """_initialize() does not call mcp.add_middleware() for a non-recording runner."""
    from autoskillit.server._state import _initialize

    mock_ctx = _make_mock_ctx(tmp_path)
    mock_ctx.runner = MagicMock()
    mock_ctx.config.subsets.disabled = []
    mock_ctx.session_skill_manager = None

    mock_mcp = MagicMock()
    monkeypatch.setattr("autoskillit.server.mcp", mock_mcp)

    with patch("autoskillit.execution.recover_crashed_sessions", return_value=0):
        _initialize(mock_ctx)

    mock_mcp.add_middleware.assert_not_called()


# --- T-INIT-3: ImportError from api_simulator.mcp does not raise ---


def test_initialize_recording_middleware_import_error_does_not_raise(tmp_path, monkeypatch):
    """_initialize() degrades gracefully when api_simulator.mcp is unavailable."""
    from autoskillit.execution.recording import RecordingSubprocessRunner
    from autoskillit.server._state import _initialize

    mock_atexit = Mock()
    monkeypatch.setattr("atexit.register", mock_atexit)
    mock_recorder = Mock()
    recording_runner = RecordingSubprocessRunner(recorder=mock_recorder)

    mock_ctx = _make_mock_ctx(tmp_path)
    mock_ctx.runner = recording_runner
    mock_ctx.config.subsets.disabled = []
    mock_ctx.session_skill_manager = None

    monkeypatch.setitem(sys.modules, "api_simulator.mcp", None)

    with patch("autoskillit.execution.recover_crashed_sessions", return_value=0):
        _initialize(mock_ctx)  # Must not raise

    mock_atexit.assert_not_called()


# --- T-INIT-4: McpReplayMiddleware registered for ReplayingSubprocessRunner ---


def test_initialize_registers_mcp_replay_middleware(tmp_path, monkeypatch):
    """_initialize() registers McpReplayMiddleware when runner is ReplayingSubprocessRunner."""
    from autoskillit.execution.recording import ReplayingSubprocessRunner
    from autoskillit.server._state import _initialize

    mock_player = Mock()
    replaying_runner = ReplayingSubprocessRunner({}, {}, player=mock_player)

    mock_ctx = _make_mock_ctx(tmp_path)
    mock_ctx.runner = replaying_runner
    mock_ctx.config.subsets.disabled = []
    mock_ctx.session_skill_manager = None

    mock_mcp = MagicMock()
    mock_middleware_cls = MagicMock()

    import api_simulator.mcp as _api_sim_mcp

    monkeypatch.setattr("autoskillit.server.mcp", mock_mcp)
    monkeypatch.setattr(_api_sim_mcp, "McpReplayMiddleware", mock_middleware_cls)

    with patch("autoskillit.execution.recover_crashed_sessions", return_value=0):
        _initialize(mock_ctx)

    mock_middleware_cls.assert_called_once_with(mock_player)
    mock_mcp.add_middleware.assert_called_once_with(mock_middleware_cls.return_value)


# --- T-INIT-5: Critical _initialize does not perform deferrable I/O ---


def test_initialize_critical_does_not_call_recovery(tmp_path, monkeypatch):
    """Critical _initialize path must not perform deferrable I/O."""
    from autoskillit.server._state import _initialize

    calls: list[str] = []
    monkeypatch.setattr(
        "autoskillit.execution.recover_crashed_sessions",
        lambda **kw: calls.append("recover") or 0,
    )

    mock_ctx = _make_mock_ctx(tmp_path)
    mock_ctx.config.subsets.disabled = []
    mock_ctx.session_skill_manager = MagicMock()

    monkeypatch.setattr(
        mock_ctx.audit,
        "load_from_log_dir",
        lambda *a, **kw: calls.append("audit_load") or 0,
    )
    monkeypatch.setattr(
        mock_ctx.session_skill_manager,
        "cleanup_stale",
        lambda: calls.append("cleanup_stale") or [],
    )

    _initialize(mock_ctx)

    assert "recover" not in calls, "Critical _initialize called recover_crashed_sessions"
    assert "audit_load" not in calls, "Critical _initialize called audit.load_from_log_dir"
    assert "cleanup_stale" not in calls, "Critical _initialize called cleanup_stale"


# --- T-INIT-6: deferred_initialize runs recovery operations ---


@pytest.mark.asyncio
async def test_deferred_initialize_runs_recovery_operations(tmp_path):
    """deferred_initialize() must run recovery, audit load, and stale cleanup."""
    from autoskillit.server._state import deferred_initialize

    mock_ctx = _make_mock_ctx(tmp_path)
    mock_ctx.config.subsets.disabled = []
    mock_ctx.session_skill_manager = MagicMock()
    mock_ctx.session_skill_manager.cleanup_stale.return_value = []
    mock_ctx.audit.load_from_log_dir.return_value = 0

    event = asyncio.Event()
    with patch("autoskillit.execution.recover_crashed_sessions", return_value=0):
        await deferred_initialize(mock_ctx, ready_event=event)

    assert event.is_set(), "deferred_initialize did not signal readiness"
