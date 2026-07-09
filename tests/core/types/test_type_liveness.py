"""Structural tests for src/autoskillit/core/types/_type_liveness.py.

Slice A of the rectify_codex_l2_attempt_liveness plan introduces the
frozen, slotted value types that codify the new liveness policy boundary:

- ChildTransportSpec
- NestedSessionSpec
- AttemptLivenessSpec
- AttemptSeed
- OperationObservation
- LivenessObservation
- LivenessDecision
- BackendPreLaunchSpec
- LivenessDiagnostics
- SessionLivenessDiagnostics

These tests prove only that the types exist, are frozen + slotted, and
produce their declared fields with the correct defaults. The integration
behavior is exercised by the slice-specific tests built alongside each
slice of the plan.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass
from types import MappingProxyType

import pytest

from autoskillit.core.types import (
    ATTEMPT_ID_ENV_VAR,
    NESTED_SESSION_SPEC_ENV_VAR,
    SESSION_DEADLINE_ENV_VAR,
    AttemptLivenessSpec,
    AttemptSeed,
    BackendPreLaunchSpec,
    ChildTransportSpec,
    LivenessDecision,
    LivenessDiagnostics,
    LivenessObservation,
    NestedSessionSpec,
    OperationObservation,
    SessionLivenessDiagnostics,
)


@pytest.mark.parametrize(
    "type_obj",
    [
        ChildTransportSpec,
        NestedSessionSpec,
        AttemptLivenessSpec,
        AttemptSeed,
        OperationObservation,
        LivenessObservation,
        LivenessDecision,
        BackendPreLaunchSpec,
        LivenessDiagnostics,
        SessionLivenessDiagnostics,
    ],
)
def test_liveness_type_is_frozen_dataclass(type_obj: type) -> None:
    """Every liveness type is @dataclass(frozen=True, slots=True)."""
    assert is_dataclass(type_obj)
    frozen_meta = getattr(type_obj, "__dataclass_params__", None)
    assert frozen_meta is not None
    assert frozen_meta.frozen is True, f"{type_obj.__name__} must be frozen"
    assert getattr(type_obj, "__slots__", None) is not None, f"{type_obj.__name__} must be slotted"


def test_child_transport_spec_carries_native_scalars_only() -> None:
    """ChildTransportSpec owns the transport scalars; no parent-policy fields."""
    spec = ChildTransportSpec(exit_after_stop_delay_ms=5000, stream_idle_timeout_ms=30000)
    assert spec.exit_after_stop_delay_ms == 5000
    assert spec.stream_idle_timeout_ms == 30000
    field_names = {f.name for f in fields(spec)}
    # No parent-policy fields leaked into child transport.
    assert "idle_output_timeout" not in field_names
    assert "max_suppression_seconds" not in field_names
    with pytest.raises(FrozenInstanceError):
        spec.exit_after_stop_delay_ms = 1  # type: ignore[misc]


def test_nested_session_spec_preserves_explicit_zero() -> None:
    """NestedSessionSpec.idle_output_timeout survives explicit 0.0."""
    spec = NestedSessionSpec(idle_output_timeout=0.0)
    assert spec.idle_output_timeout == 0.0
    spec_none = NestedSessionSpec()
    assert spec_none.idle_output_timeout is None


def test_attempt_liveness_spec_defaults_are_distinct() -> None:
    """AttemptLivenessSpec holds the resolved policy values verbatim."""
    spec = AttemptLivenessSpec(
        backend_name="codex",
        session_kind="fleet",
        caller_idle_output_timeout=1800.0,
        effective_stale_threshold=1200.0,
        mcp_tool_timeout_sec=14364.0,
        effective_wall_timeout=7200.0,
        max_extension_seconds=7200.0,
        enable_deadline_extension=True,
        max_suppression_seconds=1800.0,
    )
    assert spec.backend_name == "codex"
    assert spec.session_kind == "fleet"
    assert spec.enable_deadline_extension is True


def test_attempt_seed_carries_sole_child_env() -> None:
    """AttemptSeed.child_env is immutable Mapping (copy-on-create)."""
    env_input = {"FOO": "bar"}
    seed = AttemptSeed(
        attempt_id="attempt-1",
        child_env=MappingProxyType(env_input),
        liveness_spec=AttemptLivenessSpec(backend_name="codex"),
        wall_duration=1800.0,
        wall_ceiling=9000.0,
        extensions_enabled=True,
    )
    assert seed.attempt_id == "attempt-1"
    assert seed.child_env["FOO"] == "bar"
    with pytest.raises(FrozenInstanceError):
        seed.attempt_id = "attempt-2"  # type: ignore[misc]


def test_liveness_decision_verb_values_match_contract() -> None:
    """LivenessDecision.verb is one of the documented coordinator verbs."""
    verbs = {"CONTINUE", "REQUEST_INSPECTION", "EXTEND_TO", "TERMINATE"}
    for verb in verbs:
        d = LivenessDecision(verb=verb)
        assert d.verb == verb


def test_backend_pre_launch_spec_requires_configured_timeout() -> None:
    """BackendPreLaunchSpec.mcp_tool_timeout_sec is required (no default)."""
    spec = BackendPreLaunchSpec(mcp_tool_timeout_sec=14364.0)
    assert spec.mcp_tool_timeout_sec == 14364.0


def test_session_liveness_diagnostics_holds_ordered_attempts() -> None:
    """SessionLivenessDiagnostics.items is an immutable tuple."""
    d1 = LivenessDiagnostics(attempt_id="a1", backend_name="codex")
    d2 = LivenessDiagnostics(attempt_id="a2", backend_name="codex")
    history = SessionLivenessDiagnostics(items=(d1, d2))
    assert len(history.items) == 2
    assert history.items[0].attempt_id == "a1"
    with pytest.raises((AttributeError, FrozenInstanceError)):
        history.items = ()  # type: ignore[misc]


def test_env_var_constants_are_distinct_keys() -> None:
    """NESTED_SESSION_SPEC_ENV_VAR, ATTEMPT_ID_ENV_VAR, SESSION_DEADLINE_ENV_VAR."""
    constants = {NESTED_SESSION_SPEC_ENV_VAR, ATTEMPT_ID_ENV_VAR, SESSION_DEADLINE_ENV_VAR}
    assert len(constants) == 3
    assert NESTED_SESSION_SPEC_ENV_VAR == "AUTOSKILLIT_NESTED_SESSION_SPEC"
    assert ATTEMPT_ID_ENV_VAR == "AUTOSKILLIT_ATTEMPT_ID"
    assert SESSION_DEADLINE_ENV_VAR == "AUTOSKILLIT_SESSION_DEADLINE"


def test_required_and_optional_forward_sets_are_disjoint() -> None:
    """Required vs optional env forwarding splits must not overlap."""
    from autoskillit.core.types import (
        CODEX_MCP_OPTIONAL_ENV_FORWARD_VARS,
        CODEX_MCP_REQUIRED_ENV_FORWARD_VARS,
    )

    required = set(CODEX_MCP_REQUIRED_ENV_FORWARD_VARS)
    optional = set(CODEX_MCP_OPTIONAL_ENV_FORWARD_VARS)
    assert required.isdisjoint(optional), (
        f"Required keys leaked into optional set: {required & optional}"
    )
    # The optional set is meant to carry runtime-injected per-attempt keys.
    assert ATTEMPT_ID_ENV_VAR in optional
    assert SESSION_DEADLINE_ENV_VAR in optional
    assert NESTED_SESSION_SPEC_ENV_VAR in optional


def test_operation_observation_fields_are_observation_only() -> None:
    """OperationObservation carries raw observation data only — no policy fields."""
    obs = OperationObservation(
        operation_id="op-1",
        kind="mcp_tool_call",
        transition="started",
        raw={"type": "item.started"},
        start_monotonic=1.0,
        hard_deadline_monotonic=14365.0,
    )
    assert obs.operation_id == "op-1"
    field_names = {f.name for f in fields(obs)}
    # No completion/write/success evidence fields.
    for bad in ("completed", "is_success", "is_write", "exit_code"):
        assert bad not in field_names, (
            f"OperationObservation leaked liveness evidence field: {bad}"
        )
