"""Tests for per-dispatch typed backend authority."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from autoskillit.core import PreLaunchReadiness
from autoskillit.fleet.state_types import _RETRY_IDENTITY_FIELDS, DispatchRecord

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _mock_backend(name: str = "claude-code", *, food_truck_capable: bool = True):
    """Build a mock CodingAgentBackend with configurable capabilities."""
    backend = Mock()
    backend.name = name
    backend.capabilities.food_truck_capable = food_truck_capable
    backend.capabilities.anthropic_provider_capable = True
    backend.capabilities.has_unguarded_filesystem_access = False
    backend.capabilities.process_name = "claude"
    backend.session_locator.return_value = Mock(
        project_log_dir=Mock(return_value=Path("/tmp/logs")),
        session_log_path=Mock(return_value=None),
    )
    backend.ensure_pre_launch.return_value = PreLaunchReadiness((), {})
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


def _caller_authority(backend: str):
    from autoskillit.core import BackendAuthority, BackendAuthorityKind, BackendAuthorityTier

    return BackendAuthority(
        backend=backend,
        kind=BackendAuthorityKind.CALLER,
        tier=BackendAuthorityTier.CALLER,
        key_path="test.backend",
    )


class TestFoodTruckBackendOverridePrelaunch:
    @pytest.mark.anyio
    async def test_non_global_claude_dispatch_skips_interactive_prelaunch(
        self,
        tool_ctx,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import json

        from autoskillit.core import SubprocessResult, TerminationReason
        from tests.fakes import MockSubprocessRunner

        executor = tool_ctx.executor
        _setup(tool_ctx, monkeypatch)
        tool_ctx.executor = executor
        backend = tool_ctx.launch_resolver.backend_for_authority(_caller_authority("claude-code"))
        monkeypatch.setattr(
            type(backend),
            "ensure_pre_launch",
            lambda _self, **_kwargs: pytest.fail("Claude food-truck dispatch must skip prelaunch"),
        )
        runner = MockSubprocessRunner()
        runner.set_default(
            SubprocessResult(
                returncode=0,
                stdout=json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "result": "L3 done %%FT_DONE%%",
                        "session_id": "ft-session",
                        "is_error": False,
                    }
                ),
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=1234,
            )
        )
        tool_ctx.runner = runner
        tool_ctx.backend = backend

        await _run_with_backend(tool_ctx, dispatch_backend=backend)

        assert runner.call_args_list

    @pytest.mark.anyio
    async def test_non_global_codex_dispatch_still_runs_prelaunch(
        self,
        tool_ctx,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from autoskillit.core import SubprocessResult, TerminationReason
        from tests.fakes import MockSubprocessRunner

        executor = tool_ctx.executor
        _setup(tool_ctx, monkeypatch)
        tool_ctx.executor = executor
        backend = tool_ctx.launch_resolver.backend_for_authority(_caller_authority("codex"))
        prelaunch = Mock(return_value=PreLaunchReadiness((), {}))
        monkeypatch.setattr(type(backend), "ensure_pre_launch", prelaunch)
        runner = MockSubprocessRunner()
        runner.set_default(
            SubprocessResult(
                returncode=0,
                stdout="",
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=1234,
            )
        )
        tool_ctx.runner = runner
        tool_ctx.backend = backend

        await _run_with_backend(tool_ctx, dispatch_backend=backend)

        assert runner.call_args_list
        prelaunch.assert_called_once_with()


class TestDispatchBackendOverrideThreadsToExecutor:
    @pytest.mark.anyio
    async def test_dispatch_backend_override_threads_to_executor(self, tool_ctx, monkeypatch):
        _setup(tool_ctx, monkeypatch)
        mock_codex = _mock_backend("codex")
        tool_ctx.backend = _mock_backend("claude-code")

        await _run_with_backend(tool_ctx, dispatch_backend=mock_codex)

        assert len(tool_ctx.executor.dispatch_calls) == 1
        dispatch_call = tool_ctx.executor.dispatch_calls[0]
        assert dispatch_call.backend_authority.backend == "codex"
        assert dispatch_call.backend_authority.kind.value == "caller"
        assert dispatch_call.plugin_authority is not None
        assert dispatch_call.capability_preparation is not None


class TestDispatchBackendOverrideOmittedUsesCtxBackend:
    @pytest.mark.anyio
    async def test_dispatch_backend_override_omitted_uses_ctx_backend(self, tool_ctx, monkeypatch):
        _setup(tool_ctx, monkeypatch)
        tool_ctx.backend = _mock_backend("claude-code")

        await _run_with_backend(tool_ctx)

        assert len(tool_ctx.executor.dispatch_calls) == 1
        assert tool_ctx.executor.dispatch_calls[0].backend_authority is None


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
            authority = kwargs.get("backend_authority")
            if authority is not None and authority.backend == "incapable":
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
