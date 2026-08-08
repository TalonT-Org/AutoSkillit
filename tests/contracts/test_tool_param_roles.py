"""Role-derivation contract: ToolParamRole is the single classification authority (#4402).

Every attestation-relevant surface must derive from ``ToolParamDef.role``
rather than maintaining its own hand-copied param list: the runtime gate's
always-admit set (``runtime_exempt_param_names``), the execution-tuning
fallback tables in ``tools_execution.py``, the pre-existing
``RUN_SKILL_ATTESTATION_PARAMS`` frozenset, and ``compute_tool_contract_identity``
(which must explicitly exclude ``role`` — it is server-side policy, not wire
shape). This module pins each derivation so a future edit to any one surface
that silently diverges from the registry fails here first.
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from dataclasses import replace

import pytest

from autoskillit.core import (
    RUN_SKILL_ATTESTATION_PARAMS,
    ToolParamRole,
    compute_tool_contract_identity,
    get_tool_def,
    runtime_exempt_param_names,
)
from autoskillit.recipe.schema import RecipeStep
from autoskillit.server.tools.tools_execution import (
    _EXECUTION_TUNING_EXTERNALLY_RESOLVED,
    _EXECUTION_TUNING_STEP_FIELDS,
    _build_actual_mcp_kwargs,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def _run_skill_tool_def():
    tool_def = get_tool_def("run_skill")
    assert tool_def is not None, "run_skill must be a registered ToolDef"
    return tool_def


def test_every_run_skill_param_has_a_role() -> None:
    """role is a required kw-only field — this should be structurally impossible to fail."""
    for param in _run_skill_tool_def().params:
        assert isinstance(param.role, ToolParamRole), (
            f"{param.name} has no ToolParamRole; ToolParamDef's role default may have changed"
        )


def test_gate_admit_set_matches_role_derivation() -> None:
    """runtime_exempt_param_names (the symbol _binding.py's gate consumes) must equal
    exactly the PROTOCOL | ORCHESTRATOR_SCOPING name set derived locally from role."""
    tool_def = _run_skill_tool_def()
    expected = frozenset(
        param.name
        for param in tool_def.params
        if param.role in (ToolParamRole.PROTOCOL, ToolParamRole.ORCHESTRATOR_SCOPING)
    )
    assert runtime_exempt_param_names(tool_def) == expected


def test_execution_tuning_fallback_tables_cover_role_exactly() -> None:
    """The EXECUTION_TUNING param set equals the *disjoint* union of the two
    tools_execution.py fallback tables' keys, and every mapped RecipeStep field
    name is real. Disjointness matters: a param in both tables, or in neither,
    is a coverage bug."""
    tool_def = _run_skill_tool_def()
    execution_tuning_names = frozenset(
        param.name for param in tool_def.params if param.role is ToolParamRole.EXECUTION_TUNING
    )
    loop_keys = frozenset(_EXECUTION_TUNING_STEP_FIELDS)
    external_keys = frozenset(_EXECUTION_TUNING_EXTERNALLY_RESOLVED)

    overlap = loop_keys & external_keys
    assert not overlap, (
        f"param(s) declared in BOTH execution-tuning fallback tables: {sorted(overlap)}"
    )

    combined = loop_keys | external_keys
    assert combined == execution_tuning_names, (
        "EXECUTION_TUNING params and the fallback-table keys have drifted. "
        f"EXECUTION_TUNING but in neither table: {sorted(execution_tuning_names - combined)}. "
        f"In a table but not EXECUTION_TUNING-roled: {sorted(combined - execution_tuning_names)}."
    )

    recipe_step_field_names = frozenset(field.name for field in dataclass_fields(RecipeStep))
    mapped_fields = frozenset(_EXECUTION_TUNING_STEP_FIELDS.values()) | frozenset(
        _EXECUTION_TUNING_EXTERNALLY_RESOLVED.values()
    )
    unmapped = mapped_fields - recipe_step_field_names
    assert not unmapped, (
        f"execution-tuning table value(s) name fields RecipeStep does not declare: "
        f"{sorted(unmapped)}"
    )


def test_build_actual_mcp_kwargs_fails_fast_on_drift() -> None:
    """The kwargs-assembly helper must raise on a missing handler param and on an
    unknown values key, and must NOT demand values for dispatch_items
    (handler_parameter=False) or skill_inputs (structured_skill_inputs=True) —
    pinning the filter that keeps assembly aligned with the real handler surface."""
    tool_def = _run_skill_tool_def()
    handler_param_names = frozenset(
        param.name
        for param in tool_def.params
        if param.handler_parameter and not param.structured_skill_inputs
    )
    assert "dispatch_items" not in handler_param_names
    assert "skill_inputs" not in handler_param_names

    complete_values: dict[str, object] = dict.fromkeys(handler_param_names, "")

    incomplete = dict(complete_values)
    del incomplete["order_id"]
    with pytest.raises(ValueError, match="order_id"):
        _build_actual_mcp_kwargs(tool_def, incomplete)

    excess = dict(complete_values)
    excess["not_a_real_param"] = ""
    with pytest.raises(ValueError, match="not_a_real_param"):
        _build_actual_mcp_kwargs(tool_def, excess)

    result = _build_actual_mcp_kwargs(tool_def, complete_values)
    assert set(result) <= handler_param_names


def test_run_skill_attestation_params_is_subset_of_protocol_role() -> None:
    """RUN_SKILL_ATTESTATION_PARAMS (a pre-existing hand-maintained frozenset of 2 of
    the 3 protocol names) must remain a subset of the role-derived PROTOCOL set, so a
    protocol-param rename cannot desync this fifth surface silently."""
    tool_def = _run_skill_tool_def()
    protocol_names = frozenset(
        param.name for param in tool_def.params if param.role is ToolParamRole.PROTOCOL
    )
    assert RUN_SKILL_ATTESTATION_PARAMS <= protocol_names, (
        f"RUN_SKILL_ATTESTATION_PARAMS {sorted(RUN_SKILL_ATTESTATION_PARAMS)} is not a "
        f"subset of the PROTOCOL-role param names {sorted(protocol_names)}"
    )


def test_contract_identity_excludes_role() -> None:
    """role must not participate in compute_tool_contract_identity's hash input.

    Structural check, not a hardcoded digest: build a variant ToolDef whose
    ONLY difference from the real one is a single param's role, and assert
    the identities are equal — proving role cannot affect the hash.
    """
    tool_def = _run_skill_tool_def()
    original_param = tool_def.param_def("order_id")
    assert original_param is not None
    reroled_param = replace(original_param, role=ToolParamRole.SESSION_FLOW)
    reroled_params = tuple(
        reroled_param if param.name == "order_id" else param for param in tool_def.params
    )
    reroled_tool_def = replace(tool_def, params=reroled_params)

    assert compute_tool_contract_identity(reroled_tool_def) == compute_tool_contract_identity(
        tool_def
    ), "role changed compute_tool_contract_identity's output — it must be excluded by construction"
