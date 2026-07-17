"""Tests for per-dispatch backend override (heterogeneous routing R1)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from autoskillit.fleet.state_types import _RETRY_IDENTITY_FIELDS, DispatchRecord

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _mock_backend(name: str = "claude-code", *, food_truck_capable: bool = True):
    """Build a mock CodingAgentBackend with configurable capabilities."""
    backend = Mock()
    backend.name = name
    backend.capabilities.food_truck_capable = food_truck_capable
    backend.capabilities.anthropic_provider_capable = True
    backend.capabilities.git_metadata_writable = True
    backend.capabilities.has_unguarded_filesystem_access = False
    backend.capabilities.process_name = "claude"
    backend.session_locator.return_value = Mock(
        project_log_dir=Mock(return_value=Path("/tmp/logs")),
        session_log_path=Mock(return_value=None),
    )
    backend.ensure_pre_launch.return_value = []
    backend.build_food_truck_cmd.return_value = Mock(
        cmd=["claude", "--headless"],
        env={},
    )
    return backend


def _setup(tool_ctx, monkeypatch):
    from tests.fleet._helpers import _setup_dispatch

    _setup_dispatch(tool_ctx, monkeypatch)


async def _run_with_backend(tool_ctx, dispatch_backend=None):
    import json

    from autoskillit.fleet._api import execute_dispatch
    from tests.fleet._helpers import (
        _no_sleep_quota_checker,
        _noop_quota_refresher,
        _simple_prompt_builder,
    )

    result = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="test-recipe",
        task="t",
        ingredients=None,
        dispatch_name=None,
        timeout_sec=None,
        prompt_builder=_simple_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
        dispatch_backend=dispatch_backend,
    )
    return json.loads(result.outcome.to_envelope())


class TestDispatchBackendOverrideThreadsToExecutor:
    @pytest.mark.anyio
    async def test_dispatch_backend_override_threads_to_executor(self, tool_ctx, monkeypatch):
        _setup(tool_ctx, monkeypatch)
        mock_codex = _mock_backend("codex")
        tool_ctx.backend = _mock_backend("claude-code")

        await _run_with_backend(tool_ctx, dispatch_backend=mock_codex)

        assert len(tool_ctx.executor.dispatch_calls) == 1
        assert tool_ctx.executor.dispatch_calls[0].backend_override == "codex"


class TestDispatchBackendOverrideOmittedUsesCtxBackend:
    @pytest.mark.anyio
    async def test_dispatch_backend_override_omitted_uses_ctx_backend(self, tool_ctx, monkeypatch):
        _setup(tool_ctx, monkeypatch)
        tool_ctx.backend = _mock_backend("claude-code")

        await _run_with_backend(tool_ctx)

        assert len(tool_ctx.executor.dispatch_calls) == 1
        assert tool_ctx.executor.dispatch_calls[0].backend_override is None


class TestDispatchBackendOverrideCapabilityOverridesUseDispatchBackend:
    @pytest.mark.anyio
    async def test_dispatch_backend_override_capability_overrides_use_dispatch_backend(
        self, tool_ctx, monkeypatch
    ):
        _setup(tool_ctx, monkeypatch)
        ctx_backend = _mock_backend("claude-code")
        ctx_backend.capabilities.git_metadata_writable = False
        tool_ctx.backend = ctx_backend

        dispatch_be = _mock_backend("codex")
        dispatch_be.capabilities.git_metadata_writable = True

        await _run_with_backend(tool_ctx, dispatch_backend=dispatch_be)

        assert len(tool_ctx.executor.dispatch_calls) == 1


class TestDispatchBackendOverrideFoodTruckIncapableRaises:
    @pytest.mark.anyio
    async def test_dispatch_backend_override_food_truck_incapable_raises(
        self, tool_ctx, monkeypatch
    ):
        """When executor raises RuntimeError for incapable backend, dispatch returns rejection."""
        _setup(tool_ctx, monkeypatch)
        tool_ctx.backend = _mock_backend("claude-code")

        original_dispatch = tool_ctx.executor.dispatch_food_truck

        async def _raising_dispatch(*args, **kwargs):
            if kwargs.get("backend_override") == "incapable":
                raise RuntimeError(
                    "backend does not support food truck dispatch "
                    "(food_truck_capable=False); got 'incapable'"
                )
            return await original_dispatch(*args, **kwargs)

        tool_ctx.executor.dispatch_food_truck = _raising_dispatch

        incapable = _mock_backend("incapable", food_truck_capable=False)
        envelope = await _run_with_backend(tool_ctx, dispatch_backend=incapable)
        assert envelope.get("success") is False
        assert "food_truck_capable=False" in envelope.get("user_visible_message", "")


class TestDispatchRecordPersistsBackendName:
    @pytest.mark.anyio
    async def test_dispatch_record_persists_backend_name(self, tool_ctx, monkeypatch):
        from tests.fleet._helpers import _read_dispatch_record

        _setup(tool_ctx, monkeypatch)
        tool_ctx.backend = _mock_backend("claude-code")
        dispatch_be = _mock_backend("codex")

        await _run_with_backend(tool_ctx, dispatch_backend=dispatch_be)

        record = _read_dispatch_record(tool_ctx)
        assert record["backend_name"] == "codex"
        assert record["caller_backend_name"] == "claude-code"


class TestDispatchRecordBackendNameDefaultsEmptyWhenOmitted:
    @pytest.mark.anyio
    async def test_dispatch_record_backend_name_defaults_to_ctx_backend_when_omitted(
        self, tool_ctx, monkeypatch
    ):
        from tests.fleet._helpers import _read_dispatch_record

        _setup(tool_ctx, monkeypatch)
        tool_ctx.backend = _mock_backend("claude-code")

        await _run_with_backend(tool_ctx)

        record = _read_dispatch_record(tool_ctx)
        assert record["backend_name"] == "claude-code"


class TestDispatchBackendOverrideSessionLocatorUsesDispatchBackend:
    @pytest.mark.anyio
    async def test_dispatch_backend_override_session_locator_uses_dispatch_backend(
        self, tool_ctx, monkeypatch
    ):
        from pathlib import Path
        from unittest.mock import Mock as M

        from tests.fleet._helpers import _read_dispatch_record

        _setup(tool_ctx, monkeypatch)

        ctx_locator = M()
        ctx_locator.project_log_dir.return_value = Path("/claude-logs")
        ctx_locator.session_log_path.return_value = None
        ctx_backend = _mock_backend("claude-code")
        ctx_backend.session_locator.return_value = ctx_locator
        tool_ctx.backend = ctx_backend

        dispatch_locator = M()
        dispatch_locator.project_log_dir.return_value = Path("/codex-logs")
        dispatch_locator.session_log_path.return_value = None
        dispatch_be = _mock_backend("codex")
        dispatch_be.session_locator.return_value = dispatch_locator

        await _run_with_backend(tool_ctx, dispatch_backend=dispatch_be)

        record = _read_dispatch_record(tool_ctx)
        assert "codex-logs" in record["dispatched_session_log_dir"]


class TestDispatchBackendOverrideInvalidNameRaises:
    def test_dispatch_backend_override_invalid_name_raises(self):
        from autoskillit.server._misc import resolve_backend_override

        with pytest.raises(ValueError, match="Unknown backend"):
            resolve_backend_override("nonexistent")


class TestDispatchBackendOverridePreservedAcrossRetry:
    def test_dispatch_backend_override_preserved_across_retry(self):
        assert "backend_name" in _RETRY_IDENTITY_FIELDS
        assert "caller_backend_name" in _RETRY_IDENTITY_FIELDS

        record = DispatchRecord(
            name="test",
            backend_name="codex",
            caller_backend_name="claude-code",
        )
        d = record.to_dict()
        assert d["backend_name"] == "codex"
        assert d["caller_backend_name"] == "claude-code"

        restored = DispatchRecord.from_dict(d)
        assert restored.backend_name == "codex"
        assert restored.caller_backend_name == "claude-code"
