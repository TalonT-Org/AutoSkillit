"""Contracts for resolved headless liveness budgets."""

import pytest
import structlog.testing

from autoskillit.config import AutomationConfig
from autoskillit.core import LivenessSource
from autoskillit.execution.headless._headless_liveness import (
    DEFAULT_LEGAL_SILENCE_FLOOR_SEC,
    ResolverInputs,
    compute_legal_silence_window,
    resolve_session_liveness_spec,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def _resolver_inputs_from_config(
    cfg: AutomationConfig,
    *,
    is_food_truck: bool,
    caller_idle_output_timeout: float | None = None,
    enable_deadline_extension: bool | None = None,
) -> ResolverInputs:
    rs = cfg.run_skill
    fl = cfg.fleet
    return ResolverInputs(
        run_skill_idle_output_timeout=float(rs.idle_output_timeout),
        run_skill_stream_idle_timeout_ms=int(rs.stream_idle_timeout_ms),
        run_skill_stale_threshold=float(rs.stale_threshold),
        run_skill_max_suppression_seconds=float(rs.max_suppression_seconds),
        run_skill_mcp_tool_timeout_sec=float(rs.mcp_tool_timeout_sec),
        run_skill_timeout=float(rs.timeout),
        fleet_idle_output_timeout=float(fl.idle_output_timeout),
        fleet_default_timeout_sec=float(fl.default_timeout_sec),
        fleet_max_extension_seconds=float(fl.max_extension_seconds),
        enable_deadline_extension=(
            fl.enable_deadline_extension
            if enable_deadline_extension is None
            else enable_deadline_extension
        ),
        caller_idle_output_timeout=caller_idle_output_timeout,
        caller_session_id="caller-default",
        is_food_truck=is_food_truck,
    )


class TestResolvedLivenessBudgetCoherence:
    """Effective liveness budgets are validated from the resolved session spec."""

    def test_default_liveness_resolution_emits_no_warnings(self) -> None:
        """Default skill and food-truck liveness specs are warning-free."""
        cfg = AutomationConfig()
        with structlog.testing.capture_logs() as cap_logs:
            for is_food_truck in (False, True):
                resolve_session_liveness_spec(
                    cfg,
                    is_food_truck=is_food_truck,
                    caller_idle_output_timeout=None,
                    caller_session_id="caller-default",
                    enable_deadline_extension=cfg.fleet.enable_deadline_extension,
                )
        warning_events = [e for e in cap_logs if e.get("log_level") == "warning"]
        assert warning_events == []

    @pytest.mark.parametrize("is_food_truck", [False, True])
    def test_resolved_operation_deadline_uses_legal_silence_window(
        self, is_food_truck: bool
    ) -> None:
        cfg = AutomationConfig()
        spec = resolve_session_liveness_spec(
            cfg,
            is_food_truck=is_food_truck,
            caller_idle_output_timeout=None,
            caller_session_id="caller-default",
            enable_deadline_extension=cfg.fleet.enable_deadline_extension,
        )
        legal_silence = compute_legal_silence_window(
            _resolver_inputs_from_config(cfg, is_food_truck=is_food_truck)
        )

        assert spec.operation_deadline_sec == (legal_silence + DEFAULT_LEGAL_SILENCE_FLOOR_SEC)
        assert spec.mcp_tool_timeout_sec == cfg.run_skill.mcp_tool_timeout_sec
        expected_idle = (
            cfg.fleet.idle_output_timeout if is_food_truck else cfg.run_skill.idle_output_timeout
        )
        assert spec.stdout_idle_timeout_sec == float(expected_idle)
        assert spec.stdout_idle_timeout_sec < legal_silence
        assert LivenessSource.OPERATION_IN_FLIGHT in spec.authorized_sources

    def test_explicit_zero_idle_keeps_deadline_budget_but_disables_outer_idle(self) -> None:
        cfg = AutomationConfig()
        spec = resolve_session_liveness_spec(
            cfg,
            is_food_truck=False,
            caller_idle_output_timeout=0,
            caller_session_id="caller-default",
            enable_deadline_extension=cfg.fleet.enable_deadline_extension,
        )
        legal_silence = compute_legal_silence_window(
            _resolver_inputs_from_config(
                cfg,
                is_food_truck=False,
                caller_idle_output_timeout=0,
            )
        )

        assert spec.stdout_idle_timeout_sec is None
        assert spec.explicit_idle_disabled
        assert spec.operation_deadline_sec == (legal_silence + DEFAULT_LEGAL_SILENCE_FLOOR_SEC)
