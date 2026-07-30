"""Managed native-shell capture lineage at the food-truck boundary."""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

import pytest

from autoskillit.core import (
    ManagedHeadlessSessionKind,
    ManagedHeadlessSessionLineageStatus,
    ManagedHeadlessSessionTerminalState,
    NativeShellCaptureMode,
    resolve_native_shell_capture_decision,
)
from autoskillit.fleet import DispatchRecord, DispatchStatus, execute_dispatch, read_state
from autoskillit.fleet.state import write_initial_state
from tests.fakes import _DEFAULT_SKILL_RESULT
from tests.fleet._helpers import (
    _make_completed_clean,
    _mock_backend_with_locator,
    _no_sleep_quota_checker,
    _noop_quota_refresher,
    _setup_dispatch,
    _simple_prompt_builder,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


async def _execute(
    tool_ctx,
    *,
    mode: NativeShellCaptureMode | None = None,
    resume_session_id: str | None = None,
    prior_dispatch_id: str | None = None,
):
    return await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="test-recipe",
        task="task",
        ingredients=None,
        dispatch_name="dispatch",
        timeout_sec=None,
        prompt_builder=_simple_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
        native_shell_capture_mode=mode,
        resume_session_id=resume_session_id,
        prior_dispatch_id=prior_dispatch_id,
    )


def _seed_resume_lineage(
    tool_ctx,
    *,
    dispatch_id: str,
    session_id: str,
    mode: NativeShellCaptureMode,
) -> None:
    lineage = tool_ctx.managed_headless_session_lineage_store.create(
        lineage_anchor=tool_ctx.project_dir.resolve(),
        launch_id="a" * 32,
        decision=resolve_native_shell_capture_decision(mode),
        backend="mock-backend",
        session_kind=ManagedHeadlessSessionKind.FOOD_TRUCK,
        dispatch_id=dispatch_id,
    )
    lineage = tool_ctx.managed_headless_session_lineage_store.bind_final_native_session_id(
        lineage_anchor=tool_ctx.project_dir.resolve(),
        launch_id=lineage.launch_id,
        session_id=session_id,
        expected_generation=lineage.generation,
        expected_record_digest=lineage.record_digest,
    )
    dispatches_dir = tool_ctx.temp_dir / "dispatches"
    dispatches_dir.mkdir(parents=True, exist_ok=True)
    write_initial_state(
        dispatches_dir / f"{dispatch_id}.json",
        tool_ctx.kitchen_id,
        "campaign",
        "",
        [
            DispatchRecord(
                name="dispatch",
                status=DispatchStatus.RESUMABLE,
                dispatch_id=dispatch_id,
                dispatched_session_id=session_id,
                backend_name="mock-backend",
                managed_lineage_ref=lineage.reference,
            )
        ],
    )


class TestFoodTruckManagedLineage:
    @pytest.mark.anyio
    async def test_fresh_direct_dispatch_creates_and_persists_food_truck_lineage(
        self, tool_ctx, monkeypatch, tmp_path: Path
    ) -> None:
        _setup_dispatch(tool_ctx, monkeypatch)
        marker_dir = tmp_path / "markers"
        marker_dir.mkdir()
        tool_ctx.backend = _mock_backend_with_locator(project_log_dir=marker_dir)

        result = await _execute(tool_ctx, mode=NativeShellCaptureMode.DIRECT)

        call = tool_ctx.executor.dispatch_calls[0]
        assert call.native_shell_capture_decision.mode is NativeShellCaptureMode.DIRECT
        assert call.managed_lineage_ref is not None
        assert result.per_dispatch_state_path is not None
        state = read_state(result.per_dispatch_state_path)
        assert state is not None
        record = state.dispatches[0]
        assert record.managed_lineage_ref == call.managed_lineage_ref
        lineage = tool_ctx.managed_headless_session_lineage_store.load_reference(
            call.managed_lineage_ref
        )
        assert lineage.session_kind is ManagedHeadlessSessionKind.FOOD_TRUCK
        assert lineage.dispatch_id == record.dispatch_id
        assert lineage.decision.mode is NativeShellCaptureMode.DIRECT
        assert lineage.terminal_state is ManagedHeadlessSessionTerminalState.FAILED

    @pytest.mark.anyio
    async def test_valid_resume_reuses_verified_direct_lineage_and_rejects_override(
        self, tool_ctx, monkeypatch, tmp_path: Path
    ) -> None:
        import structlog.testing

        _setup_dispatch(tool_ctx, monkeypatch)
        session_log = tmp_path / "resume.jsonl"
        session_log.write_text("{}\n")
        tool_ctx.backend = _mock_backend_with_locator(
            project_log_dir=tmp_path,
            session_log_path=session_log,
        )
        dispatch_id = "11111111-1111-4111-8111-111111111111"
        _seed_resume_lineage(
            tool_ctx,
            dispatch_id=dispatch_id,
            session_id="native-session",
            mode=NativeShellCaptureMode.DIRECT,
        )

        with structlog.testing.capture_logs() as logs:
            result = await _execute(
                tool_ctx,
                mode=NativeShellCaptureMode.CAPTURE,
                resume_session_id="native-session",
                prior_dispatch_id=dispatch_id,
            )

        assert tool_ctx.executor.dispatch_calls, result.outcome
        call = tool_ctx.executor.dispatch_calls[0]
        assert call.dispatch_id == dispatch_id
        assert call.resume_session_id == "native-session"
        assert call.native_shell_capture_decision.mode is NativeShellCaptureMode.DIRECT
        assert call.managed_lineage_ref.launch_id == "a" * 32
        assert any(
            entry.get("event") == "native_shell_capture_resume_override_rejected" for entry in logs
        )

    @pytest.mark.anyio
    async def test_native_session_mismatch_degrades_to_fresh_capture_identity(
        self, tool_ctx, monkeypatch, tmp_path: Path
    ) -> None:
        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.backend = _mock_backend_with_locator(project_log_dir=tmp_path)
        prior_dispatch_id = "11111111-1111-4111-8111-111111111111"
        _seed_resume_lineage(
            tool_ctx,
            dispatch_id=prior_dispatch_id,
            session_id="trusted-native-session",
            mode=NativeShellCaptureMode.DIRECT,
        )

        result = await _execute(
            tool_ctx,
            mode=NativeShellCaptureMode.DIRECT,
            resume_session_id="untrusted-native-session",
            prior_dispatch_id=prior_dispatch_id,
        )

        assert tool_ctx.executor.dispatch_calls, result.outcome
        call = tool_ctx.executor.dispatch_calls[0]
        assert call.dispatch_id != prior_dispatch_id
        assert call.resume_session_id is None
        assert call.native_shell_capture_decision.mode is NativeShellCaptureMode.CAPTURE
        assert (
            call.native_shell_capture_decision.lineage_status
            is ManagedHeadlessSessionLineageStatus.NATIVE_SESSION_MISMATCH
        )
        assert call.managed_lineage_ref.launch_id != "a" * 32
        lineage = tool_ctx.managed_headless_session_lineage_store.load_reference(
            call.managed_lineage_ref
        )
        assert lineage.dispatch_id == call.dispatch_id
        assert lineage.final_native_session_id is None

    @pytest.mark.anyio
    async def test_cancellation_closes_lineage_as_cancelled(
        self, tool_ctx, monkeypatch, tmp_path: Path
    ) -> None:
        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.backend = _mock_backend_with_locator(project_log_dir=tmp_path)

        async def _cancel(**_kwargs):
            raise asyncio.CancelledError

        monkeypatch.setattr(tool_ctx.executor, "dispatch_food_truck", _cancel)

        with pytest.raises(asyncio.CancelledError):
            await _execute(tool_ctx)

        state_files = list((tool_ctx.temp_dir / "dispatches").glob("*.json"))
        assert len(state_files) == 1
        state = read_state(state_files[0])
        assert state is not None
        reference = state.dispatches[0].managed_lineage_ref
        assert reference is not None
        lineage = tool_ctx.managed_headless_session_lineage_store.load_reference(reference)
        assert lineage.terminal_state is ManagedHeadlessSessionTerminalState.CANCELLED

    @pytest.mark.anyio
    async def test_executor_candidate_and_final_bindings_share_persisted_lineage(
        self, tool_ctx, monkeypatch, tmp_path: Path
    ) -> None:
        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.backend = _mock_backend_with_locator(project_log_dir=tmp_path)

        async def _bind_native_session(**kwargs):
            reference = kwargs["managed_lineage_ref"]
            store = tool_ctx.managed_headless_session_lineage_store
            lineage = store.load_reference(reference)
            lineage = store.bind_candidate_native_session_id(
                lineage_anchor=Path(lineage.lineage_anchor),
                launch_id=lineage.launch_id,
                session_id="candidate-native",
                expected_generation=lineage.generation,
                expected_record_digest=lineage.record_digest,
            )
            store.bind_final_native_session_id(
                lineage_anchor=Path(lineage.lineage_anchor),
                launch_id=lineage.launch_id,
                session_id="final-native",
                expected_generation=lineage.generation,
                expected_record_digest=lineage.record_digest,
            )
            return dataclasses.replace(
                _DEFAULT_SKILL_RESULT,
                success=False,
                session_id="final-native",
            )

        monkeypatch.setattr(
            tool_ctx.executor,
            "dispatch_food_truck",
            _bind_native_session,
        )

        result = await _execute(tool_ctx)

        assert result.per_dispatch_state_path is not None
        state = read_state(result.per_dispatch_state_path)
        assert state is not None
        reference = state.dispatches[0].managed_lineage_ref
        assert reference is not None
        lineage = tool_ctx.managed_headless_session_lineage_store.load_reference(reference)
        assert lineage.candidate_native_session_ids == (
            "candidate-native",
            "final-native",
        )
        assert lineage.final_native_session_id == "final-native"

    @pytest.mark.anyio
    async def test_first_terminal_provenance_is_not_overwritten_by_fleet_classification(
        self, tool_ctx, monkeypatch, tmp_path: Path
    ) -> None:
        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.backend = _mock_backend_with_locator(project_log_dir=tmp_path)

        async def _close_failed_first(**kwargs):
            reference = kwargs["managed_lineage_ref"]
            store = tool_ctx.managed_headless_session_lineage_store
            lineage = store.load_reference(reference)
            store.set_terminal_state(
                lineage_anchor=Path(lineage.lineage_anchor),
                launch_id=lineage.launch_id,
                terminal_state=ManagedHeadlessSessionTerminalState.FAILED,
                expected_generation=lineage.generation,
                expected_record_digest=lineage.record_digest,
            )
            return dataclasses.replace(_DEFAULT_SKILL_RESULT, success=True)

        monkeypatch.setattr(
            tool_ctx.executor,
            "dispatch_food_truck",
            _close_failed_first,
        )
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_completed_clean(success=True),
        )

        result = await _execute(tool_ctx)

        assert result.per_dispatch_state_path is not None
        state = read_state(result.per_dispatch_state_path)
        assert state is not None
        reference = state.dispatches[0].managed_lineage_ref
        assert reference is not None
        lineage = tool_ctx.managed_headless_session_lineage_store.load_reference(reference)
        assert lineage.terminal_state is ManagedHeadlessSessionTerminalState.FAILED

    @pytest.mark.anyio
    async def test_concurrent_dispatches_keep_lineage_and_dispatch_identity_isolated(
        self, tool_ctx, monkeypatch, tmp_path: Path
    ) -> None:
        from autoskillit.fleet import FleetSemaphore

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=2)
        tool_ctx.backend = _mock_backend_with_locator(project_log_dir=tmp_path)

        first, second = await asyncio.gather(
            _execute(tool_ctx, mode=NativeShellCaptureMode.CAPTURE),
            _execute(tool_ctx, mode=NativeShellCaptureMode.DIRECT),
        )

        calls = tool_ctx.executor.dispatch_calls
        assert len(calls) == 2
        assert calls[0].dispatch_id != calls[1].dispatch_id
        assert calls[0].managed_lineage_ref != calls[1].managed_lineage_ref
        assert {
            calls[0].native_shell_capture_decision.mode,
            calls[1].native_shell_capture_decision.mode,
        } == {
            NativeShellCaptureMode.CAPTURE,
            NativeShellCaptureMode.DIRECT,
        }
        assert first.per_dispatch_state_path != second.per_dispatch_state_path

    @pytest.mark.anyio
    async def test_lineage_initialization_failure_does_not_leak_diagnostics(
        self, tool_ctx, monkeypatch, tmp_path: Path
    ) -> None:
        from autoskillit.core import FleetErrorCode
        from autoskillit.fleet import DispatchRejected

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.backend = _mock_backend_with_locator(project_log_dir=tmp_path)
        diagnostic = (
            "requested_mode=direct effective_mode=capture "
            "reason=invalid_lineage lineage_status=corrupt "
            "launch_id=feedfacefeedfacefeedfacefeedface "
            "attempt_id=deaddeaddeaddeaddeaddeaddeaddead"
        )

        def _fail_create(**_kwargs):
            raise ValueError(diagnostic)

        monkeypatch.setattr(
            tool_ctx.managed_headless_session_lineage_store,
            "create",
            _fail_create,
        )

        result = await _execute(tool_ctx, mode=NativeShellCaptureMode.DIRECT)

        assert isinstance(result.outcome, DispatchRejected)
        assert result.outcome.error_code == FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH
        assert result.outcome.message == "Food-truck dispatch initialization failed."
        assert "feedface" not in result.outcome.to_envelope()
        assert "deaddead" not in result.outcome.to_envelope()
        assert result.per_dispatch_state_path is not None
        state = read_state(result.per_dispatch_state_path)
        assert state is not None
        record = state.dispatches[0]
        assert record.reason == FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH
        assert record.diagnostic_message == "Food-truck dispatch initialization failed."

    @pytest.mark.anyio
    async def test_outer_crash_boundary_does_not_leak_lineage_validation_text(
        self, tool_ctx, monkeypatch
    ) -> None:
        from autoskillit.core import FleetErrorCode
        from autoskillit.fleet import DispatchRejected

        _setup_dispatch(tool_ctx, monkeypatch)
        diagnostic = (
            "managed lineage validation failed "
            "launch_id=feedfacefeedfacefeedfacefeedface "
            "attempt_id=deaddeaddeaddeaddeaddeaddeaddead"
        )

        async def _fail_dispatch(**_kwargs):
            raise RuntimeError(diagnostic)

        monkeypatch.setattr(
            "autoskillit.fleet._api._run_dispatch",
            _fail_dispatch,
        )

        result = await _execute(tool_ctx)

        assert isinstance(result.outcome, DispatchRejected)
        assert result.outcome.error_code == FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH
        assert result.outcome.message == "Food-truck dispatch failed during startup."
        assert "feedface" not in result.outcome.to_envelope()
        assert "deaddead" not in result.outcome.to_envelope()
