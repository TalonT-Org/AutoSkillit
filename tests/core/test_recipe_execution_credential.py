"""Recipe execution credential projections."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    BindingMode,
    BoundStepInvocation,
    BoundValue,
    BoundValueOrigin,
    BoundValueState,
    InvocationTemplate,
    RecipeExecutionSnapshot,
    build_recipe_execution_credential,
    compute_invocation_template_digest,
    compute_recipe_execution_snapshot_digest,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_recipe_execution_credential_projects_ordered_keys_and_falsey_defaults() -> None:
    values = (
        BoundValue(
            name="empty",
            declared_value="${{ context.empty }}",
            effective_value="${{ context.empty }}",
            state=BoundValueState.PRESENT,
            origin=BoundValueOrigin.CONTEXT,
            context_dependencies=("empty",),
            unresolved_default="",
        ),
        BoundValue(
            name="zero",
            declared_value="${{ context.zero }}",
            effective_value="${{ context.zero }}",
            state=BoundValueState.PRESENT,
            origin=BoundValueOrigin.CONTEXT,
            context_dependencies=("zero",),
            unresolved_default=0,
        ),
        BoundValue(
            name="disabled",
            declared_value="${{ context.disabled }}",
            effective_value="${{ context.disabled }}",
            state=BoundValueState.PRESENT,
            origin=BoundValueOrigin.CONTEXT,
            context_dependencies=("disabled",),
            unresolved_default=False,
        ),
        BoundValue(
            name="resolved",
            declared_value="ready",
            effective_value="ready",
            state=BoundValueState.PRESENT,
            origin=BoundValueOrigin.LITERAL,
        ),
        BoundValue.absent("undeclared"),
    )
    invocation = BoundStepInvocation(
        step_name="invoke",
        tool_name="run_skill",
        mode=BindingMode.RECIPE,
        skill_name="demo-skill",
        mcp_kwargs=(),
        skill_inputs=values,
    )
    execution_id = "execution-1"
    content_hash = "sha256:" + "c" * 64
    composite_hash = "sha256:" + "d" * 64
    tool_identity = "tool-v1"
    skill_identity = "skill-v1"
    template_digest = compute_invocation_template_digest(
        execution_id=execution_id,
        recipe_name="demo",
        content_hash=content_hash,
        composite_hash=composite_hash,
        invocation=invocation,
        tool_contract_identity=tool_identity,
        skill_contract_identity=skill_identity,
    )
    template = InvocationTemplate(
        invocation=invocation,
        tool_contract_identity=tool_identity,
        skill_contract_identity=skill_identity,
        template_digest=template_digest,
    )
    templates = {"invoke": template}
    snapshot = RecipeExecutionSnapshot(
        execution_id=execution_id,
        recipe_name="demo",
        content_hash=content_hash,
        composite_hash=composite_hash,
        templates=templates,
        snapshot_digest=compute_recipe_execution_snapshot_digest(
            execution_id=execution_id,
            recipe_name="demo",
            content_hash=content_hash,
            composite_hash=composite_hash,
            templates=templates,
        ),
    )

    credential = build_recipe_execution_credential(snapshot)
    wire = credential.as_wire_block()

    expected_shapes = {
        "invoke": {
            "keys": ["empty", "zero", "disabled", "resolved"],
            "unresolved_defaults": {"empty": "", "zero": 0, "disabled": False},
        }
    }
    assert wire["skill_input_shapes"] == expected_shapes

    wire["skill_input_shapes"]["invoke"]["keys"].append("mutated")
    wire["skill_input_shapes"]["invoke"]["unresolved_defaults"]["empty"] = "mutated"

    assert credential.as_wire_block()["skill_input_shapes"] == expected_shapes
