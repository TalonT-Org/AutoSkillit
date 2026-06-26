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


class TestKindDiscriminatorEnvelopeField:
    def test_kind_discriminator_present_in_both_envelopes(self):
        """Both DispatchCompleted and DispatchRejected must carry a 'kind' discriminator
        in their envelope. This explicit discriminator is what allows the formatter to
        distinguish them when both have success=False.
        """
        from autoskillit.core import FleetErrorCode
        from autoskillit.fleet import DispatchCompleted, DispatchRejected, DispatchStatus

        completed = DispatchCompleted(
            success=True,
            dispatch_status=DispatchStatus.SUCCESS,
            dispatch_id="d-c",
            dispatched_session_id="s-c",
            reason="ok",
        )
        completed_env = json.loads(completed.to_envelope())
        assert completed_env.get("kind") == "completed", (
            f"DispatchCompleted.to_envelope() must emit 'kind': 'completed': {completed_env}"
        )

        rejected = DispatchRejected(
            error_code=FleetErrorCode.FLEET_QUOTA_EXHAUSTED,
            message="quota hit",
            dispatch_id="d-r",
        )
        rejected_env = json.loads(rejected.to_envelope())
        assert rejected_env.get("kind") == "rejected", (
            f"DispatchRejected.to_envelope() must emit 'kind': 'rejected': {rejected_env}"
        )

    def test_rejected_envelope_never_emits_completed_exclusive_fields(self):
        """DispatchRejected.to_envelope() must never emit DispatchCompleted-exclusive
        fields. This is a structural exclusion guard that protects the discriminator
        invariant: if a future change adds dispatch_status to DispatchRejected, this
        test fails immediately.
        """
        from autoskillit.core import FleetErrorCode
        from autoskillit.fleet import DispatchRejected

        rejected = DispatchRejected(
            error_code=FleetErrorCode.FLEET_QUOTA_EXHAUSTED,
            message="quota hit",
            dispatch_id="d-r",
        )
        env = json.loads(rejected.to_envelope())
        excluded_keys = {
            "dispatch_status",
            "dispatched_session_id",
            "reason",
            "token_usage",
            "l3_payload",
            "l3_parse_source",
            "lifespan_started",
            "stderr",
            "elapsed_seconds",
        }
        leaked = excluded_keys & set(env.keys())
        assert not leaked, (
            f"DispatchRejected.to_envelope() leaked DispatchCompleted-exclusive fields: "
            f"{sorted(leaked)}. The 'kind' discriminator invariant is broken."
        )


class TestHealthReportEnvelopeField:
    def test_envelope_includes_health_report_when_present(self):
        from autoskillit.fleet import DispatchCompleted, DispatchStatus

        completed = DispatchCompleted(
            success=True,
            dispatch_status=DispatchStatus.SUCCESS,
            dispatch_id="test-id",
            dispatched_session_id="sess-id",
            reason="",
            health_report={"findings": [], "summary": "clean"},
        )
        result = json.loads(completed.to_envelope())
        assert "health_report" in result
        assert result["health_report"]["summary"] == "clean"

    def test_envelope_omits_health_report_when_none(self):
        from autoskillit.fleet import DispatchCompleted, DispatchStatus

        completed = DispatchCompleted(
            success=True,
            dispatch_status=DispatchStatus.SUCCESS,
            dispatch_id="test-id",
            dispatched_session_id="sess-id",
            reason="",
        )
        result = json.loads(completed.to_envelope())
        assert "health_report" not in result


class TestReadHealthReport:
    def test_returns_parsed_report_when_file_exists(self, tmp_path):
        reports_dir = tmp_path / "health-reports"
        reports_dir.mkdir()
        report = {"kitchen_id": "k1", "dispatch_id": "d1", "findings": [], "summary": "ok"}
        (reports_dir / "d1_health_report.json").write_text(json.dumps(report))
        from autoskillit.server.tools.tools_fleet_dispatch import _read_health_report

        result = _read_health_report(tmp_path, "d1")
        assert result == report

    def test_returns_none_when_file_missing(self, tmp_path):
        from autoskillit.server.tools.tools_fleet_dispatch import _read_health_report

        result = _read_health_report(tmp_path, "d1")
        assert result is None

    def test_returns_none_on_malformed_json(self, tmp_path):
        reports_dir = tmp_path / "health-reports"
        reports_dir.mkdir()
        (reports_dir / "d1_health_report.json").write_text("not json")
        from autoskillit.server.tools.tools_fleet_dispatch import _read_health_report

        result = _read_health_report(tmp_path, "d1")
        assert result is None


class TestHealthReportDispatchEnrichment:
    def test_dispatch_completed_enriched_with_health_report(self):
        from dataclasses import replace

        from autoskillit.fleet import DispatchCompleted, DispatchStatus

        original = DispatchCompleted(
            success=True,
            dispatch_status=DispatchStatus.SUCCESS,
            dispatch_id="test-id",
            dispatched_session_id="sess-id",
            reason="",
        )
        report = {"findings": [{"severity": "anomaly", "summary": "test"}]}
        enriched = replace(original, health_report=report)
        envelope = json.loads(enriched.to_envelope())
        assert envelope["health_report"] == report
        assert envelope["dispatch_id"] == "test-id"
