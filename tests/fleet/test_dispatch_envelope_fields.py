"""Dispatch envelope field persistence tests for fleet dispatch."""

from __future__ import annotations

import json

import pytest

from tests.fleet._helpers import (
    _make_completed_clean,
    _make_completed_dirty,
    _make_no_sentinel,
    _run,
    _setup_dispatch,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


class TestElapsedSecondsEnvelopeField:
    @pytest.mark.anyio
    async def test_dispatch_completed_envelope_contains_elapsed_seconds(self):
        """DispatchCompleted.to_envelope() includes elapsed_seconds field.

        Regression test: elapsed_seconds is computed in _run_dispatch() but never
        added to DispatchCompleted, so the L3 agent has no timing source.
        """
        from autoskillit.fleet import DispatchCompleted, DispatchStatus

        completed = DispatchCompleted(
            success=True,
            dispatch_status=DispatchStatus.SUCCESS,
            dispatch_id="test-dispatch-id",
            dispatched_session_id="test-session-id",
            reason="",
            token_usage={"input": 100, "output": 50, "cache_read": 10, "cache_creation": 5},
            elapsed_seconds=42.5,
        )
        result = json.loads(completed.to_envelope())
        assert "elapsed_seconds" in result, "to_envelope() must include elapsed_seconds"
        assert result["elapsed_seconds"] == 42.5


class TestDispatchStatusEnvelopeField:
    @pytest.mark.anyio
    async def test_envelope_includes_dispatch_status_on_success(self, tool_ctx, monkeypatch):
        """Envelope from _run_dispatch includes dispatch_status matching state-file status."""
        _setup_dispatch(tool_ctx, monkeypatch)
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_completed_clean(success=True),
        )

        result = await _run(tool_ctx)
        assert "dispatch_status" in result
        assert result["dispatch_status"] == "success"

    @pytest.mark.anyio
    async def test_envelope_includes_dispatch_status_on_failure(self, tool_ctx, monkeypatch):
        """Envelope includes dispatch_status='failure' when outcome is completed_dirty."""
        _setup_dispatch(tool_ctx, monkeypatch)
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_completed_dirty(),
        )

        result = await _run(tool_ctx)
        assert "dispatch_status" in result
        assert result["dispatch_status"] == "failure"

    @pytest.mark.anyio
    async def test_envelope_includes_dispatch_status_on_no_sentinel(self, tool_ctx, monkeypatch):
        """Envelope includes dispatch_status='failure' for no_sentinel without session signal."""
        _setup_dispatch(tool_ctx, monkeypatch)
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        result = await _run(tool_ctx)
        assert "dispatch_status" in result
        assert result["dispatch_status"] == "failure"

    @pytest.mark.anyio
    async def test_envelope_includes_dispatch_status_on_no_sentinel_resumable(
        self, tool_ctx, monkeypatch
    ):
        """no_sentinel + session_id + lifespan_started + sidecar → dispatch_status='resumable'."""
        import dataclasses

        from autoskillit.core import DispatchIdentity
        from autoskillit.fleet.sidecar import sidecar_path
        from tests.fakes import _DEFAULT_SKILL_RESULT, InMemoryHeadlessExecutor

        _setup_dispatch(tool_ctx, monkeypatch)

        fixed_dispatch_id = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
        _fixed_identity = DispatchIdentity.from_dispatch_id(fixed_dispatch_id)

        class _FixedDispatchIdentity:
            @classmethod
            def fresh(cls) -> DispatchIdentity:
                return _fixed_identity

        monkeypatch.setattr("autoskillit.fleet.state.DispatchIdentity", _FixedDispatchIdentity)

        sidecar_file = sidecar_path(fixed_dispatch_id, tool_ctx.project_dir)
        sidecar_file.parent.mkdir(parents=True, exist_ok=True)
        sidecar_file.touch()

        resumable_result = dataclasses.replace(
            _DEFAULT_SKILL_RESULT,
            session_id="sess-resumable-abc",
            lifespan_started=True,
        )

        class _SpawningExecutor(InMemoryHeadlessExecutor):
            """Calls on_spawn with a fake PID to drive PENDING → RUNNING before returning."""

            async def dispatch_food_truck(self, orchestrator_prompt, cwd, *, on_spawn=None, **kw):
                if on_spawn is not None:
                    on_spawn(12345, 1000)
                return await super().dispatch_food_truck(
                    orchestrator_prompt, cwd, on_spawn=on_spawn, **kw
                )

        tool_ctx.executor = _SpawningExecutor(default_result=resumable_result)

        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        result = await _run(tool_ctx)
        assert "dispatch_status" in result
        assert result["dispatch_status"] == "resumable"
