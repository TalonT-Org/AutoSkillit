"""Semantic validation rules — loop artifact scope enforcement."""

from __future__ import annotations

from autoskillit.core import SKILL_TOOLS, Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._contracts_manifest import get_skill_contract, load_bundled_manifest
from autoskillit.recipe._rule_helpers import _find_cycle_members
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule

_ARTIFACT_OUTPUT_TYPES = frozenset({"directory_path", "file_path", "file_path_list"})


@semantic_rule(
    name="loop-iterated-step-requires-iteration-scoped-output",
    description=(
        "run_skill steps inside routing cycles must use iteration-scoped output_dir "
        "to prevent artifact collision between loop iterations"
    ),
    severity=Severity.ERROR,
)
def _check_loop_artifact_scope(ctx: ValidationContext) -> list[RuleFinding]:
    """Flag run_skill steps in cycles that have a static (non-iteration-scoped) output_dir."""
    findings: list[RuleFinding] = []
    recipe_steps = ctx.recipe.steps

    cycle_sets = _find_cycle_members(ctx.step_graph, recipe_steps)
    manifest = load_bundled_manifest()
    examined_artifact_steps: set[str] = set()

    for cycle_set in cycle_sets:
        for step_name in cycle_set:
            if step_name in examined_artifact_steps:
                continue
            step = recipe_steps.get(step_name)
            if step is None:
                continue
            if step.tool not in SKILL_TOOLS:
                continue
            invocation = ctx.binding_projection.for_step(step_name)
            if invocation is None or invocation.skill_name is None:
                continue
            contract = get_skill_contract(invocation.skill_name, manifest)
            if contract is None:
                continue
            artifact_outputs = tuple(
                output for output in contract.outputs if output.type in _ARTIFACT_OUTPUT_TYPES
            )
            if not artifact_outputs:
                # Some skills mutate a declared input in place. Their output_dir
                # is a write boundary, not an artifact destination.
                continue
            if any(output.name == "audit_cycle_path" for output in artifact_outputs):
                # Audit-cycle outputs are already content-addressed beneath an
                # immutable cycle identity rooted at output_dir.
                continue
            examined_artifact_steps.add(step_name)
            output_dir = next(
                (
                    value
                    for value in invocation.mcp_kwargs
                    if value.name == "output_dir" and value.is_present
                ),
                None,
            )
            if output_dir is None:
                continue
            if "AUTOSKILLIT_TEMP" not in output_dir.template_dependencies:
                continue
            effective_output_dir = output_dir.effective_value
            if not isinstance(effective_output_dir, str):
                continue
            if output_dir.context_dependencies:
                continue
            cycle_list = sorted(cycle_set)
            findings.append(
                make_finding(
                    rule_name="loop-iterated-step-requires-iteration-scoped-output",
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' is in a loop cycle but uses a static "
                        f"output_dir '{effective_output_dir}'. Loop-iterated run_skill steps must "
                        f"include an iteration-scoped context variable in output_dir "
                        f"(e.g., '${{{{ context.review_loop_count }}}}') to prevent "
                        f"artifact collision. Cycle: [{'→'.join(cycle_list)}]"
                    ),
                )
            )

    return findings
