"""Pre-flight JSONL validation and session chain integrity tests for fleet resume."""

from __future__ import annotations

from pathlib import Path

import pytest
import structlog.testing

from tests.fleet._helpers import (
    _make_no_sentinel,
    _mock_backend_with_locator,
    _no_sleep_quota_checker,
    _noop_quota_refresher,
    _setup_dispatch,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


class TestResumeJSONLPreflight:
    @pytest.mark.anyio
    async def test_missing_lineage_degrades_to_fresh_capture_before_jsonl_preflight(
        self, tool_ctx, monkeypatch, tmp_path
    ):
        """A session log alone cannot authorize resume without durable lineage."""
        from autoskillit.core import (
            ManagedHeadlessSessionLineageStatus,
            NativeShellCaptureMode,
        )
        from autoskillit.fleet import DispatchRecord, write_initial_state
        from autoskillit.fleet._api import execute_dispatch

        _setup_dispatch(tool_ctx, monkeypatch)

        dispatches_dir = tool_ctx.temp_dir / "dispatches"
        dispatches_dir.mkdir(parents=True, exist_ok=True)
        prior_id = "00000000-0000-0000-0000-000000000101"
        write_initial_state(
            dispatches_dir / f"{prior_id}.json",
            tool_ctx.kitchen_id,
            "camp",
            "",
            [DispatchRecord(name="test-recipe")],
        )

        tool_ctx.backend = _mock_backend_with_locator(
            project_log_dir=tmp_path, session_log_path=None
        )

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=lambda **_: "prompt",
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
            resume_session_id="nonexistent-uuid",
            prior_dispatch_id=prior_id,
        )

        assert len(tool_ctx.executor.dispatch_calls) == 1
        call = tool_ctx.executor.dispatch_calls[0]
        assert call.resume_session_id is None
        assert call.native_shell_capture_decision.mode is NativeShellCaptureMode.CAPTURE
        assert (
            call.native_shell_capture_decision.lineage_status
            is ManagedHeadlessSessionLineageStatus.MISSING
        )

    @pytest.mark.anyio
    async def test_existing_jsonl_proceeds_past_preflight(self, tool_ctx, monkeypatch, tmp_path):
        """Resume dispatch with existing JSONL proceeds to executor dispatch."""
        from autoskillit.fleet._api import execute_dispatch

        jsonl_file = tmp_path / "session.jsonl"
        jsonl_file.touch()

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.backend = _mock_backend_with_locator(
            project_log_dir=tmp_path, session_log_path=jsonl_file
        )
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=lambda **_: "prompt",
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
            resume_session_id="valid-session-uuid",
            prior_dispatch_id=None,
        )

        assert len(tool_ctx.executor.dispatch_calls) == 1
        assert tool_ctx.executor.dispatch_calls[0].resume_session_id is None

    @pytest.mark.anyio
    async def test_chain_fallback_proceeds_when_primary_missing(
        self, tool_ctx, monkeypatch, tmp_path
    ):
        """When primary JSONL is missing but chain entry exists, fallback proceeds dispatch."""
        from autoskillit.fleet import DispatchRecord, DispatchStatus, write_initial_state
        from autoskillit.fleet._api import execute_dispatch
        from autoskillit.fleet.state import upsert_dispatch_record_by_name
        from autoskillit.fleet.state_types import DispatchRejected

        _setup_dispatch(tool_ctx, monkeypatch)

        dispatches_dir = tool_ctx.temp_dir / "dispatches"
        dispatches_dir.mkdir(parents=True, exist_ok=True)
        prior_id = "00000000-0000-0000-0000-000000000102"
        prev_state_path = dispatches_dir / f"{prior_id}.json"
        write_initial_state(
            prev_state_path,
            tool_ctx.kitchen_id,
            "camp",
            "",
            [DispatchRecord(name="test-recipe")],
        )
        upsert_dispatch_record_by_name(
            prev_state_path,
            DispatchRecord(
                name="test-recipe",
                status=DispatchStatus.RESUMABLE,
                session_chain=["chain-session-id"],
            ),
        )

        chain_jsonl = tmp_path / "chain.jsonl"
        chain_jsonl.touch()

        def _mock_session_log_path(_cwd: str, session_id: str) -> Path | None:
            if session_id == "nonexistent-primary":
                return tmp_path / "nonexistent.jsonl"
            if session_id == "chain-session-id":
                return chain_jsonl
            return None

        tool_ctx.backend = _mock_backend_with_locator(
            project_log_dir=tmp_path,
            session_log_path_side_effect=_mock_session_log_path,
        )
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=lambda **_: "prompt",
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
            resume_session_id="nonexistent-primary",
            prior_dispatch_id=prior_id,
        )

        assert not isinstance(result.outcome, DispatchRejected), (
            f"Expected dispatch to proceed but got rejection: {result.outcome}"
        )
        assert len(tool_ctx.executor.dispatch_calls) == 1
        assert tool_ctx.executor.dispatch_calls[0].resume_session_id is None


class TestSessionChainContinuity:
    @pytest.mark.anyio
    async def test_unverified_session_id_cannot_reach_continuity_check(
        self, tool_ctx, monkeypatch, tmp_path
    ):
        """Missing lineage clears the untrusted resume ID before execution."""
        import dataclasses

        from autoskillit.fleet._api import execute_dispatch
        from tests.fakes import _DEFAULT_SKILL_RESULT, InMemoryHeadlessExecutor

        jsonl_file = tmp_path / "session.jsonl"
        jsonl_file.touch()

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(
                _DEFAULT_SKILL_RESULT,
                session_id="returned-different-session",
            )
        )

        tool_ctx.backend = _mock_backend_with_locator(
            project_log_dir=tmp_path, session_log_path=jsonl_file
        )
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        with structlog.testing.capture_logs() as logs:
            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="t",
                ingredients=None,
                dispatch_name=None,
                timeout_sec=None,
                prompt_builder=lambda **_: "prompt",
                quota_checker=_no_sleep_quota_checker,
                quota_refresher=_noop_quota_refresher,
                resume_session_id="original-session-id",
                prior_dispatch_id=None,
            )

        mismatch_logs = [
            log for log in logs if log.get("event") == "session_id_continuity_mismatch"
        ]
        assert mismatch_logs == []
        assert tool_ctx.executor.dispatch_calls[0].resume_session_id is None


class TestResumeSuccessGuard:
    @pytest.mark.anyio
    async def test_resume_rejected_when_prior_dispatch_succeeded(self, tool_ctx, monkeypatch):
        """_run_dispatch returns cached SUCCESS when prior dispatch already succeeded."""
        from autoskillit.fleet import DispatchRecord, DispatchStatus, write_initial_state
        from autoskillit.fleet._api import execute_dispatch
        from autoskillit.fleet.state_types import DispatchCompleted

        _setup_dispatch(tool_ctx, monkeypatch)

        dispatches_dir = tool_ctx.temp_dir / "dispatches"
        dispatches_dir.mkdir(parents=True, exist_ok=True)
        prior_id = "00000000-0000-0000-0000-000000000103"
        state_path = dispatches_dir / f"{prior_id}.json"
        write_initial_state(
            state_path,
            tool_ctx.kitchen_id,
            "camp",
            "",
            [
                DispatchRecord(
                    name="test-recipe",
                    status=DispatchStatus.SUCCESS,
                    dispatch_id="completed-dispatch-id",
                    dispatched_session_id="completed-session-id",
                    reason="completed_clean",
                )
            ],
        )

        result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=lambda **_: "prompt",
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
            resume_session_id="some-session-id",
            prior_dispatch_id=prior_id,
        )

        assert isinstance(result.outcome, DispatchCompleted)
        assert result.outcome.success is True
        assert result.outcome.dispatch_status == DispatchStatus.SUCCESS
        assert result.outcome.dispatch_id == "completed-dispatch-id"
        assert result.outcome.dispatched_session_id == "completed-session-id"
        assert result.outcome.reason == "completed_clean"
        assert len(tool_ctx.executor.dispatch_calls) == 0

    @pytest.mark.anyio
    async def test_resume_proceeds_when_prior_dispatch_resumable(
        self, tool_ctx, monkeypatch, tmp_path
    ):
        """_run_dispatch proceeds normally when prior dispatch is RESUMABLE."""
        from autoskillit.fleet import DispatchRecord, DispatchStatus, write_initial_state
        from autoskillit.fleet._api import execute_dispatch

        _setup_dispatch(tool_ctx, monkeypatch)

        jsonl_file = tmp_path / "session.jsonl"
        jsonl_file.touch()
        tool_ctx.backend = _mock_backend_with_locator(
            project_log_dir=tmp_path, session_log_path=jsonl_file
        )
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        dispatches_dir = tool_ctx.temp_dir / "dispatches"
        dispatches_dir.mkdir(parents=True, exist_ok=True)
        prior_id = "00000000-0000-0000-0000-000000000104"
        state_path = dispatches_dir / f"{prior_id}.json"
        write_initial_state(
            state_path,
            tool_ctx.kitchen_id,
            "camp",
            "",
            [
                DispatchRecord(
                    name="test-recipe",
                    status=DispatchStatus.RESUMABLE,
                    dispatched_session_id="resumable-session-id",
                )
            ],
        )

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=lambda **_: "prompt",
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
            resume_session_id="resumable-session-id",
            prior_dispatch_id=prior_id,
        )

        assert len(tool_ctx.executor.dispatch_calls) == 1

    @pytest.mark.anyio
    async def test_resume_proceeds_when_prior_dispatch_not_in_state(
        self, tool_ctx, monkeypatch, tmp_path
    ):
        """_run_dispatch proceeds when prior state has no matching dispatch (fail-open)."""
        from autoskillit.fleet import DispatchRecord, DispatchStatus, write_initial_state
        from autoskillit.fleet._api import execute_dispatch

        _setup_dispatch(tool_ctx, monkeypatch)

        jsonl_file = tmp_path / "session.jsonl"
        jsonl_file.touch()
        tool_ctx.backend = _mock_backend_with_locator(
            project_log_dir=tmp_path, session_log_path=jsonl_file
        )
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        dispatches_dir = tool_ctx.temp_dir / "dispatches"
        dispatches_dir.mkdir(parents=True, exist_ok=True)
        prior_id = "00000000-0000-0000-0000-000000000105"
        state_path = dispatches_dir / f"{prior_id}.json"
        write_initial_state(
            state_path,
            tool_ctx.kitchen_id,
            "camp",
            "",
            [DispatchRecord(name="other-recipe", status=DispatchStatus.SUCCESS)],
        )

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=lambda **_: "prompt",
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
            resume_session_id="some-session-id",
            prior_dispatch_id=prior_id,
        )

        assert len(tool_ctx.executor.dispatch_calls) == 1

    @pytest.mark.anyio
    async def test_resume_proceeds_when_prior_state_missing(self, tool_ctx, monkeypatch, tmp_path):
        """_run_dispatch proceeds when prior state file does not exist (fail-open)."""
        from autoskillit.fleet._api import execute_dispatch

        _setup_dispatch(tool_ctx, monkeypatch)

        jsonl_file = tmp_path / "session.jsonl"
        jsonl_file.touch()
        tool_ctx.backend = _mock_backend_with_locator(
            project_log_dir=tmp_path, session_log_path=jsonl_file
        )
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        prior_id = "00000000-0000-0000-0000-000000000106"

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=lambda **_: "prompt",
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
            resume_session_id="some-session-id",
            prior_dispatch_id=prior_id,
        )

        assert len(tool_ctx.executor.dispatch_calls) == 1
