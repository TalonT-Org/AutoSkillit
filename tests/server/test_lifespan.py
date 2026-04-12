"""Tests that the FastMCP lifespan calls recorder.finalize() on server shutdown."""

import asyncio
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
