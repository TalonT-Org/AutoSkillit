"""Tests for the _notify notification helper."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from autoskillit.server._notify import _notify

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestNotifyHelper:
    """Unit tests for the centralized _notify() notification helper."""

    @pytest.mark.anyio
    async def test_notify_raises_value_error_for_reserved_key_name(self):
        """The 'name' key that caused the original bug must be rejected."""
        ctx = AsyncMock()
        ctx.info = AsyncMock()
        with pytest.raises(ValueError, match="reserved LogRecord"):
            await _notify(
                ctx,
                "info",
                "migrate_recipe: foo",
                "autoskillit.migrate_recipe",
                extra={"name": "foo"},
            )
        ctx.info.assert_not_awaited()

    @pytest.mark.anyio
    async def test_notify_raises_for_all_reserved_keys(self):
        """Every key in RESERVED_LOG_RECORD_KEYS must be rejected."""
        from autoskillit.core.types import RESERVED_LOG_RECORD_KEYS

        ctx = AsyncMock()
        ctx.info = AsyncMock()
        for reserved_key in RESERVED_LOG_RECORD_KEYS:
            with pytest.raises(ValueError, match="reserved LogRecord"):
                await _notify(ctx, "info", "msg", "logger", extra={reserved_key: "value"})

    @pytest.mark.anyio
    async def test_notify_accepts_safe_key_recipe_name(self):
        """'recipe_name' (the corrected key for migrate_recipe) must be accepted."""
        ctx = AsyncMock()
        ctx.info = AsyncMock()
        await _notify(
            ctx,
            "info",
            "migrate_recipe: foo",
            "autoskillit.migrate_recipe",
            extra={"recipe_name": "foo"},
        )
        ctx.info.assert_awaited_once_with(
            "migrate_recipe: foo",
            logger_name="autoskillit.migrate_recipe",
            extra={"recipe_name": "foo"},
        )

    @pytest.mark.anyio
    async def test_notify_accepts_none_extra(self):
        ctx = AsyncMock()
        ctx.info = AsyncMock()
        await _notify(ctx, "info", "msg", "logger")  # no extra
        ctx.info.assert_awaited_once()

    @pytest.mark.anyio
    async def test_notify_accepts_empty_extra(self):
        ctx = AsyncMock()
        ctx.info = AsyncMock()
        await _notify(ctx, "info", "msg", "logger", extra={})
        ctx.info.assert_awaited_once()

    @pytest.mark.anyio
    async def test_notify_swallows_attribute_error_from_ctx(self):
        """Contract: must not raise even when ctx.info raises AttributeError
        (e.g. _CurrentContext sentinel). Test completion is the assertion."""
        ctx = AsyncMock()
        ctx.info = AsyncMock(side_effect=AttributeError("no info"))
        # Must not raise
        await _notify(ctx, "info", "msg", "logger", extra={"cwd": "/tmp"})

    @pytest.mark.anyio
    async def test_notify_swallows_runtime_error_from_ctx(self):
        """Contract: must not raise even when ctx.info raises RuntimeError
        (no active MCP session). Test completion is the assertion."""
        ctx = AsyncMock()
        ctx.info = AsyncMock(side_effect=RuntimeError("session not available"))
        await _notify(ctx, "info", "msg", "logger", extra={"cwd": "/tmp"})

    @pytest.mark.anyio
    async def test_notify_swallows_key_error_from_ctx(self):
        """Contract: must not raise even when ctx.info raises KeyError
        (FastMCP stdlib logging path). Test completion is the assertion."""
        ctx = AsyncMock()
        ctx.info = AsyncMock(side_effect=KeyError("Attempt to overwrite 'name' in LogRecord"))
        await _notify(ctx, "info", "msg", "logger", extra={"cwd": "/tmp"})

    @pytest.mark.anyio
    async def test_notify_dispatches_error_level(self):
        ctx = AsyncMock()
        ctx.error = AsyncMock()
        await _notify(
            ctx,
            "error",
            "run_cmd failed",
            "autoskillit.run_cmd",
            extra={"exit_code": 1},
        )
        ctx.error.assert_awaited_once_with(
            "run_cmd failed",
            logger_name="autoskillit.run_cmd",
            extra={"exit_code": 1},
        )

    @pytest.mark.anyio
    async def test_notify_swallows_closed_resource_error_from_ctx(self):
        """Contract: must not raise when ctx.info raises ClosedResourceError
        (send end closed on MCP client disconnect)."""
        from anyio import ClosedResourceError

        ctx = AsyncMock()
        ctx.info = AsyncMock(side_effect=ClosedResourceError)
        await _notify(ctx, "info", "msg", "logger", extra={"cwd": "/tmp"})
        ctx.info.assert_awaited_once()

    @pytest.mark.anyio
    async def test_notify_swallows_broken_resource_error_from_ctx(self):
        """Contract: must not raise when ctx.info raises BrokenResourceError
        (receive end closed on MCP client disconnect)."""
        from anyio import BrokenResourceError

        ctx = AsyncMock()
        ctx.info = AsyncMock(side_effect=BrokenResourceError)
        await _notify(ctx, "info", "msg", "logger", extra={"cwd": "/tmp"})
        ctx.info.assert_awaited_once()
