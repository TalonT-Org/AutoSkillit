"""Tests that the FastMCP lifespan calls recorder.finalize() on server shutdown."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.execution.recording import RecordingSubprocessRunner


@pytest.mark.asyncio
async def test_lifespan_calls_finalize_on_recording_runner():
    """lifespan __aexit__ calls recorder.finalize() when runner is RecordingSubprocessRunner."""
    from autoskillit.server import _autoskillit_lifespan

    mock_recorder = MagicMock()
    mock_runner = MagicMock(spec=RecordingSubprocessRunner)
    mock_runner.recorder = mock_recorder
    mock_ctx = MagicMock()
    mock_ctx.runner = mock_runner

    with patch("autoskillit.server._lifespan._get_ctx_or_none", return_value=mock_ctx):
        async with _autoskillit_lifespan(MagicMock()):
            pass  # server running phase

    mock_recorder.finalize.assert_called_once()


@pytest.mark.asyncio
async def test_lifespan_skips_finalize_when_not_recording():
    """lifespan __aexit__ does not error when runner is not RecordingSubprocessRunner."""
    from autoskillit.server import _autoskillit_lifespan

    mock_ctx = MagicMock()
    mock_ctx.runner = MagicMock()  # plain runner, not RecordingSubprocessRunner

    with patch("autoskillit.server._lifespan._get_ctx_or_none", return_value=mock_ctx):
        async with _autoskillit_lifespan(MagicMock()):
            pass  # must not raise


@pytest.mark.asyncio
async def test_lifespan_skips_finalize_when_ctx_is_none():
    """lifespan __aexit__ is safe when _get_ctx_or_none() returns None (non-recording mode)."""
    from autoskillit.server import _autoskillit_lifespan

    with patch("autoskillit.server._lifespan._get_ctx_or_none", return_value=None):
        async with _autoskillit_lifespan(MagicMock()):
            pass  # must not raise


@pytest.mark.asyncio
async def test_lifespan_calls_finalize_on_cancellation():
    """finalize() is called even when the lifespan task is cancelled (SIGTERM path).

    Regression guard for issue #745: the try/finally in _autoskillit_lifespan must
    ensure finalize() runs when CancelledError is thrown at the yield point, which
    is exactly what anyio does when KeyboardInterrupt cancels the running task group.
    """
    from autoskillit.server import _autoskillit_lifespan

    mock_recorder = MagicMock()
    mock_runner = MagicMock(spec=RecordingSubprocessRunner)
    mock_runner.recorder = mock_recorder
    mock_ctx = MagicMock()
    mock_ctx.runner = mock_runner

    with patch("autoskillit.server._lifespan._get_ctx_or_none", return_value=mock_ctx):
        with pytest.raises(asyncio.CancelledError):
            async with _autoskillit_lifespan(MagicMock()):
                raise asyncio.CancelledError

    mock_recorder.finalize.assert_called_once()


@pytest.mark.asyncio
async def test_lifespan_sets_startup_ready_event():
    """_startup_ready must be set to a real Event and signalled after lifespan yield."""
    from autoskillit.server import _autoskillit_lifespan, _state

    mock_ctx = MagicMock()
    mock_ctx.runner = MagicMock()
    mock_ctx.config.linux_tracing.tmpfs_path = "/tmp"
    mock_ctx.config.linux_tracing.log_dir = None
    mock_ctx.audit = MagicMock()
    mock_ctx.audit.load_from_log_dir = MagicMock(return_value=0)
    mock_ctx.session_skill_manager = None

    # Reset _startup_ready to None before test
    original = _state._startup_ready
    _state._startup_ready = None

    try:
        with patch("autoskillit.server._lifespan._get_ctx_or_none", return_value=mock_ctx):
            async with _autoskillit_lifespan(MagicMock()):
                # After lifespan yields, _startup_ready should be set
                assert _state._startup_ready is not None, (
                    "_startup_ready must be assigned an asyncio.Event during lifespan"
                )
                assert _state._startup_ready.is_set(), (
                    "_startup_ready event must be signalled after deferred_initialize completes"
                )
    finally:
        _state._startup_ready = original


def test_serve_startup_regenerates_on_hash_mismatch(tmp_path: Path, monkeypatch) -> None:
    """run_startup_drift_check() regenerates hooks.json when hash is mismatched."""
    import json as _json

    import autoskillit.core.paths as _paths
    from autoskillit.hook_registry import HOOK_REGISTRY_HASH
    from autoskillit.server._lifespan import run_startup_drift_check

    fake_pkg_root = tmp_path / "pkg"
    hooks_dir = fake_pkg_root / "hooks"
    hooks_dir.mkdir(parents=True)
    stale_json = {"_autoskillit_registry_hash": "deadbeef", "hooks": {}}
    (hooks_dir / "hooks.json").write_text(_json.dumps(stale_json))

    monkeypatch.setattr(_paths, "pkg_root", lambda: fake_pkg_root)

    run_startup_drift_check()

    updated = _json.loads((hooks_dir / "hooks.json").read_text())
    assert updated.get("_autoskillit_registry_hash") == HOOK_REGISTRY_HASH
