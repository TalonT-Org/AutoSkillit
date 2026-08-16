"""Runtime skill invocation binding — the attestation gate's admission behavior (#4402).

``bind_runtime_skill_invocation`` is the runtime half of the compile-time
``bind_step_invocation`` covered in ``test_skill_invocation_binding.py``.
Before #4402 this had zero direct coverage anywhere under ``tests/`` — the
one e2e attestation test structurally could not reach the denial path (it
never passed a non-empty undeclared param), so compile-time coverage stood
in as an untested proxy for a runtime contract nobody actually exercised.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from autoskillit.core import (
    BoundScalar,
    InvocationTemplate,
    compute_invocation_template_digest,
    compute_tool_contract_identity,
    get_tool_def,
)
from autoskillit.recipe._binding import (
    RuntimeBindingError,
    bind_runtime_skill_invocation,
    bind_step_invocation,
)
from autoskillit.recipe._contracts_manifest import compute_skill_contract_identity
from autoskillit.recipe.schema import RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_MANIFEST = {
    "skills": {
        "dry-walkthrough": {
            "inputs": [
                {"name": "plan_path", "type": "file_path", "required": True},
                {
                    "name": "audit_cycle_path",
                    "type": "file_path",
                    "required": False,
                    "absence_value": "",
                },
            ]
        }
    }
}
_EXECUTION_ID = "exec-1"
_STEP_NAME = "verify"


def _template(
    *,
    optional_context_refs: list[str] | None = None,
    **extra_with_args: object,
) -> InvocationTemplate:
    """Build a compiled InvocationTemplate the way the production initialization
    envelope does (see server/_recipe_execution.py:build_recipe_execution_snapshot),
    without driving the full server stack."""
    step = RecipeStep(
        name=_STEP_NAME,
        tool="run_skill",
        with_args={
            "skill_command": "/autoskillit:dry-walkthrough",
            "cwd": "/repo",
            "skill_inputs": {"plan_path": "/tmp/plan.md"},
            "step_name": _STEP_NAME,
            **extra_with_args,
        },
        optional_context_refs=optional_context_refs or [],
    )
    invocation = bind_step_invocation(_STEP_NAME, step, manifest=_MANIFEST)
    assert invocation.is_valid, invocation.failures
    assert invocation.skill_name is not None
    tool_def = get_tool_def("run_skill")
    assert tool_def is not None
    tool_identity = compute_tool_contract_identity(tool_def)
    skill_identity = compute_skill_contract_identity(invocation.skill_name)
    digest = compute_invocation_template_digest(
        execution_id=_EXECUTION_ID,
        recipe_name="test-recipe",
        content_hash="c" * 64,
        composite_hash="d" * 64,
        invocation=invocation,
        tool_contract_identity=tool_identity,
        skill_contract_identity=skill_identity,
    )
    return InvocationTemplate(
        invocation=invocation,
        tool_contract_identity=tool_identity,
        skill_contract_identity=skill_identity,
        template_digest=digest,
    )


def _bind(
    template: InvocationTemplate,
    *,
    skill_inputs: Mapping[str, BoundScalar] | None = None,
    **overrides: BoundScalar,
):
    """Bind against ``template``, matching production's shape: the caller
    always supplies actual values for every compiled (with:-declared)
    param — see ``_build_actual_mcp_kwargs`` in tools_execution.py, which
    never produces a sparse dict. ``skill_command``/``cwd``/``step_name``
    are always compiled (every ``_template()`` step declares them); pass
    extra kwargs to override or to supply values for anything else under
    test."""
    actual_mcp_kwargs: dict[str, BoundScalar] = {
        "skill_command": "/autoskillit:dry-walkthrough",
        "cwd": "/repo",
        "step_name": _STEP_NAME,
    }
    actual_mcp_kwargs.update(overrides)
    return bind_runtime_skill_invocation(
        template,
        execution_id=_EXECUTION_ID,
        step_name=_STEP_NAME,
        skill_command="/autoskillit:dry-walkthrough",
        skill_inputs=skill_inputs or {"plan_path": "/tmp/plan.md"},
        actual_mcp_kwargs=actual_mcp_kwargs,
    )


def _template_with_absence_value() -> InvocationTemplate:
    return _template(
        optional_context_refs=["audit_cycle_path"],
        skill_inputs={
            "plan_path": "/tmp/plan.md",
            "audit_cycle_path": "${{ context.audit_cycle_path }}",
        },
    )


@pytest.mark.parametrize(
    "skill_inputs",
    [
        {"plan_path": "/tmp/plan.md"},
        {
            "plan_path": "/tmp/plan.md",
            "audit_cycle_path": "",
            "fabricated": "value",
        },
    ],
)
def test_runtime_binding_rejects_missing_or_fabricated_skill_input_keys(
    skill_inputs: Mapping[str, BoundScalar],
) -> None:
    template = _template_with_absence_value()

    with pytest.raises(RuntimeBindingError) as excinfo:
        _bind(template, skill_inputs=skill_inputs)

    assert excinfo.value.code == "recipe_execution_input_shape"


def test_runtime_binding_admits_explicit_advertised_default_without_auto_fill() -> None:
    template = _template_with_absence_value()
    default = template.invocation.skill_input("audit_cycle_path")
    assert default is not None and default.absence_value == ""

    bound = _bind(
        template,
        skill_inputs={"plan_path": "/tmp/plan.md", "audit_cycle_path": ""},
    )

    assert bound == (("plan_path", "/tmp/plan.md"), ("audit_cycle_path", ""))


def test_undeclared_non_empty_value_is_denied() -> None:
    """(a) characterization — passes today; unaffected by #4402."""
    template = _template()
    with pytest.raises(RuntimeBindingError) as excinfo:
        _bind(template, resume_session_id="sess-123")
    assert excinfo.value.code == "recipe_execution_tool_shape"


def test_order_id_is_admitted_with_any_value() -> None:
    """(b) FAILED before #4402 — order_id is ORCHESTRATOR_SCOPING, always
    admitted regardless of with:-declaration. Restores the #4296 escape hatch."""
    template = _template()
    result = _bind(template, order_id="AB")
    assert result == (("plan_path", "/tmp/plan.md"),)


def test_protocol_values_with_correct_bindings_are_admitted() -> None:
    """(c) characterization — the three protocol values are always admitted
    when they match the active invocation."""
    template = _template()
    result = _bind(
        template,
        step_name=_STEP_NAME,
        recipe_execution_id=_EXECUTION_ID,
        invocation_template_digest=template.template_digest,
    )
    assert result == (("plan_path", "/tmp/plan.md"),)


def test_with_declared_model_override_is_admitted() -> None:
    """(d) characterization — a with:-declared model is a genuine per-step
    call-time override channel, distinct from (and independent of) the
    top-level model: field's server-side resolution."""
    template = _template(model="sonnet")
    result = _bind(template, model="sonnet")
    assert result == (("plan_path", "/tmp/plan.md"),)


def test_undeclared_empty_string_values_are_admitted() -> None:
    """(e) characterization — "" is the omitted-param vacancy sentinel. This
    exemption is intentional and load-bearing: closing it would deny every
    ordinary call that simply omits an optional parameter it isn't using."""
    template = _template()
    result = _bind(template, resume_session_id="", step_provider="", model="")
    assert result == (("plan_path", "/tmp/plan.md"),)


@pytest.mark.parametrize(
    "param_name,value", [("stale_threshold", 2400), ("model", "claude-opus-5")]
)
def test_undeclared_execution_tuning_denial_carries_actionable_remedy(
    param_name: str, value: BoundScalar
) -> None:
    """(f) FAILED before #4402 — denial for an undeclared EXECUTION_TUNING param
    must name the parameter, state it is server-resolved, and name the with:
    override option (see _binding.py:_undeclared_runtime_param_message)."""
    template = _template()
    with pytest.raises(RuntimeBindingError) as excinfo:
        _bind(template, **{param_name: value})
    message = str(excinfo.value)
    assert excinfo.value.code == "recipe_execution_tool_shape"
    assert param_name in message
    assert "server-resolved" in message
    assert "with:" in message
