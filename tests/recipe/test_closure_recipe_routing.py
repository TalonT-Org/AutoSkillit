"""Structural tests: all six consuming recipes must wire closure ingredients to audit_impl."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    BoundScalar,
    BoundStepInvocation,
    BoundValueState,
    InvocationTemplate,
    RecipeExecutionSnapshot,
    build_recipe_execution_credential,
    compute_invocation_template_digest,
    compute_recipe_execution_snapshot_digest,
    compute_tool_contract_identity,
    get_tool_def,
)
from autoskillit.core.io import load_yaml
from autoskillit.recipe._binding import (
    RuntimeBindingError,
    bind_recipe,
    bind_runtime_skill_invocation,
)
from autoskillit.recipe._contracts_manifest import (
    compute_skill_contract_identity,
    get_skill_contract,
    load_bundled_manifest,
    select_audit_output_contract,
)
from autoskillit.recipe._contracts_types import AuditOutputMode
from autoskillit.recipe.io import load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RECIPES_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "autoskillit" / "recipes"

_AUDIT_CWD_BY_RECIPE = {
    "implementation.yaml": "${{ context.work_dir }}",
    "implementation-groups.yaml": "${{ context.work_dir }}",
    "remediation.yaml": "${{ context.work_dir }}",
    "merge-prs.yaml": "${{ context.work_dir }}",
    "research.yaml": "${{ context.worktree_path }}",
    "research-implement.yaml": "${{ context.worktree_path }}",
}
_RECIPE_FILES = list(_AUDIT_CWD_BY_RECIPE)


def _load_recipe(name: str) -> dict:
    path = _RECIPES_DIR / name
    data = load_yaml(path)
    assert isinstance(data, dict), f"{name} must parse as a mapping"
    return data


def _find_audit_impl_step(data: dict) -> dict:
    steps = data.get("steps", {})
    for step_def in steps.values():
        if not isinstance(step_def, dict):
            continue
        with_block = step_def.get("with", {})
        if not isinstance(with_block, dict):
            continue
        skill_command = with_block.get("skill_command", "")
        if isinstance(skill_command, str) and "audit-impl" in skill_command:
            return step_def
    raise AssertionError("No audit_impl step found")


@pytest.mark.parametrize("recipe_name", _RECIPE_FILES)
def test_all_six_recipes_have_closure_ingredients(recipe_name: str) -> None:
    """Each recipe has closure ingredients with empty string defaults."""
    data = _load_recipe(recipe_name)
    ingredients = data.get("ingredients", {})
    assert "closure_authority_path" in ingredients, (
        f"{recipe_name} must declare 'closure_authority_path' ingredient"
    )
    assert "closure_authority_hash" in ingredients, (
        f"{recipe_name} must declare 'closure_authority_hash' ingredient"
    )
    assert ingredients["closure_authority_path"].get("default") == "", (
        f"{recipe_name}: closure_authority_path default must be empty string"
    )
    assert ingredients["closure_authority_hash"].get("default") == "", (
        f"{recipe_name}: closure_authority_hash default must be empty string"
    )


@pytest.mark.parametrize("recipe_name", _RECIPE_FILES)
def test_audit_impl_step_has_closure_with_params(recipe_name: str) -> None:
    """audit_impl step's with: block contains closure_authority_path and closure_authority_hash."""
    data = _load_recipe(recipe_name)
    step = _find_audit_impl_step(data)
    with_block = step.get("with", {})
    assert "closure_authority_path" in with_block, (
        f"{recipe_name} audit_impl step must have 'closure_authority_path' in with: block"
    )
    assert "closure_authority_hash" in with_block, (
        f"{recipe_name} audit_impl step must have 'closure_authority_hash' in with: block"
    )
    assert "closure_plan_paths" in with_block, (
        f"{recipe_name} audit_impl step must have 'closure_plan_paths' in with: block"
    )
    assert "closure_base_sha" in with_block, (
        f"{recipe_name} audit_impl step must have 'closure_base_sha' in with: block"
    )


@pytest.mark.parametrize(
    ("recipe_name", "expected_cwd"),
    _AUDIT_CWD_BY_RECIPE.items(),
)
def test_audit_impl_compiles_to_attested_runtime_invocation(
    recipe_name: str,
    expected_cwd: str,
) -> None:
    recipe = load_recipe(_RECIPES_DIR / recipe_name)
    projection = bind_recipe(recipe)
    invocation = projection.for_step("audit_impl")

    assert isinstance(invocation, BoundStepInvocation)
    assert invocation.skill_name == "audit-impl"
    assert invocation.attested
    assert invocation.is_valid, invocation.failures
    cwd = next(value for value in invocation.mcp_kwargs if value.name == "cwd")
    assert cwd.effective_value == expected_cwd

    execution_id = f"test-{recipe.name}-execution"
    tool_def = get_tool_def(invocation.tool_name)
    assert tool_def is not None
    tool_identity = compute_tool_contract_identity(tool_def)
    manifest = load_bundled_manifest()
    skill_identity = compute_skill_contract_identity("audit-impl", manifest=manifest)
    template_digest = compute_invocation_template_digest(
        execution_id=execution_id,
        recipe_name=recipe.name,
        content_hash=recipe.content_hash,
        composite_hash=recipe.content_hash,
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
    templates = {"audit_impl": template}
    snapshot = RecipeExecutionSnapshot(
        execution_id=execution_id,
        recipe_name=recipe.name,
        content_hash=recipe.content_hash,
        composite_hash=recipe.content_hash,
        templates=templates,
        snapshot_digest=compute_recipe_execution_snapshot_digest(
            execution_id=execution_id,
            recipe_name=recipe.name,
            content_hash=recipe.content_hash,
            composite_hash=recipe.content_hash,
            templates=templates,
        ),
    )
    credential = build_recipe_execution_credential(snapshot)
    assert snapshot.execution_id == credential.execution_id == execution_id
    assert dict(credential.invocation_template_digests) == dict(snapshot.template_digests)
    assert credential.invocation_template_digests["audit_impl"] == template.template_digest

    contract = get_skill_contract("audit-impl", manifest)
    assert contract is not None
    attested_contract = select_audit_output_contract(contract, AuditOutputMode.ATTESTED)
    assert attested_contract.audit_output_mode is AuditOutputMode.ATTESTED
    publication = attested_contract.audit_authority_publication
    assert publication is not None
    assert publication.output_field == "audit_semantic_result_path"
    assert publication.prior_input_field == "prior_audit_cycle_path"

    actual_mcp_kwargs: dict[str, BoundScalar] = {}
    for value in invocation.mcp_kwargs:
        if value.state is not BoundValueState.PRESENT:
            continue
        assert isinstance(value.effective_value, (str, int, bool))
        actual_mcp_kwargs[value.name] = value.effective_value
    actual_mcp_kwargs.update(
        recipe_execution_id=credential.execution_id,
        invocation_template_digest=credential.invocation_template_digests["audit_impl"],
    )
    skill_inputs: dict[str, BoundScalar] = {}
    for value in invocation.skill_inputs:
        if value.state is not BoundValueState.PRESENT:
            continue
        assert isinstance(value.effective_value, (str, int, bool))
        skill_inputs[value.name] = value.effective_value
    assert bind_runtime_skill_invocation(
        template,
        execution_id=credential.execution_id,
        step_name="audit_impl",
        skill_command="/autoskillit:audit-impl",
        skill_inputs=skill_inputs,
        actual_mcp_kwargs=actual_mcp_kwargs,
    ) == tuple(skill_inputs.items())

    for protocol_name in (
        "recipe_execution_id",
        "invocation_template_digest",
        "step_name",
    ):
        mismatched_kwargs = dict(actual_mcp_kwargs)
        mismatched_kwargs[protocol_name] = "mismatched"
        with pytest.raises(RuntimeBindingError, match="attestation parameter"):
            bind_runtime_skill_invocation(
                template,
                execution_id=credential.execution_id,
                step_name="audit_impl",
                skill_command="/autoskillit:audit-impl",
                skill_inputs=skill_inputs,
                actual_mcp_kwargs=mismatched_kwargs,
            )


@pytest.mark.parametrize("recipe_name", _RECIPE_FILES)
def test_non_closure_preserves_routing(recipe_name: str) -> None:
    """When closure ingredients are empty, on_result routing is preserved."""
    data = _load_recipe(recipe_name)
    step = _find_audit_impl_step(data)
    on_result = step.get("on_result", [])
    assert isinstance(on_result, list), (
        f"{recipe_name} audit_impl step must have on_result as list"
    )
    assert len(on_result) > 0, f"{recipe_name} audit_impl step must have at least one routing rule"
