"""Contract tests: server._notify module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_notify_module_exports():
    from autoskillit.server._notify import _get_ctx_or_none, _notify, track_response_size

    assert callable(_notify)
    assert callable(track_response_size)
    assert callable(_get_ctx_or_none)


@pytest.mark.anyio
async def test_notify_sends_logging_not_progress():
    """`_notify()` is a logging primitive — it must never call report_progress."""
    from autoskillit.server._notify import _notify

    ctx = MagicMock(info=AsyncMock(), error=AsyncMock(), report_progress=AsyncMock())

    await _notify(ctx, "info", "hello", logger_name="test.logger")

    ctx.info.assert_awaited_once_with("hello", logger_name="test.logger", extra=None)
    ctx.report_progress.assert_not_called()
