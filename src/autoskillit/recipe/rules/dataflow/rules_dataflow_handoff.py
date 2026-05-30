"""Semantic rules for handoff consumer and merge lifecycle checks."""

from __future__ import annotations

from autoskillit.core import SKILL_TOOLS, Severity, get_logger
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import (
    get_skill_contract,
    load_bundled_manifest,
    resolve_skill_name,
)
from autoskillit.recipe.registry import RuleFinding, semantic_rule

logger = get_logger(__name__)


@semantic_rule(
    name="implicit-handoff",
    description="Skill with declared outputs missing capture block",
    severity=Severity.ERROR,
)
def _check_implicit_handoff(ctx: ValidationContext) -> list[RuleFinding]:
    """Error when a skill step has contract outputs but no capture: block."""
    wf = ctx.recipe
    try:
        manifest = load_bundled_manifest()
    except Exception:
        logger.warning("implicit-handoff: failed to load skill_contracts.yaml; skipping")
        return []

    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if not isinstance(skill_cmd, str):
            continue
        skill_name = resolve_skill_name(skill_cmd)
        if not skill_name:
            continue
        contract = manifest.get("skills", {}).get(skill_name)
        if not contract:
            continue
        if not contract.get("outputs"):
            continue
        if not step.capture and not step.capture_list:
            findings.append(
                RuleFinding(
                    rule="implicit-handoff",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' calls '/autoskillit:{skill_name}' "
                        f"which declares {len(contract['outputs'])} output(s) "
                        f"but has no capture: or capture_list: block. Add a capture: block to "
                        f"thread the skill's structured output into pipeline context."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="merge-cleanup-uncaptured",
    description="merge_worktree steps should capture cleanup_succeeded to track orphaned results.",
    severity=Severity.WARNING,
)
def _check_merge_cleanup_captured(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings: list[RuleFinding] = []

    for step_name, step in wf.steps.items():
        if step.tool != "merge_worktree":
            continue
        # Check whether any capture value references cleanup_succeeded
        captures_cleanup = any(
            "result.cleanup_succeeded" in str(v) for v in (step.capture or {}).values()
        ) or any("result.cleanup_succeeded" in str(v) for v in (step.capture_list or {}).values())
        if not captures_cleanup:
            findings.append(
                RuleFinding(
                    rule="merge-cleanup-uncaptured",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' calls merge_worktree but does not capture "
                        f"'cleanup_succeeded'. Add a capture entry such as "
                        f'cleanup_ok: "${{{{ result.cleanup_succeeded }}}}" '
                        f"so cleanup failures (orphaned worktree/branch) are not silently ignored."
                    ),
                )
            )

    return findings


@semantic_rule(
    name="stale-ref-after-merge",
    description=(
        "A context variable sourced from a worktree path or branch name is consumed "
        "by a step that executes after merge_worktree (or remove_clone), which deletes "
        "the resource the variable refers to."
    ),
    severity=Severity.WARNING,
)
def _check_stale_ref_after_merge(ctx: ValidationContext) -> list[RuleFinding]:
    return [
        RuleFinding(
            rule="stale-ref-after-merge",
            severity=Severity.WARNING,
            step_name=w.step_name,
            message=w.message,
        )
        for w in ctx.dataflow.warnings
        if w.code == "REF_INVALIDATED"
    ]


@semantic_rule(
    name="uncaptured-handoff-consumer",
    description=(
        "Skill with no declared outputs before a consumer with unwired optional file-path inputs"
    ),
    severity=Severity.WARNING,
)
def _check_uncaptured_handoff_consumer(ctx: ValidationContext) -> list[RuleFinding]:
    """Warning when an outputs:[] skill is immediately followed by a consumer with optional
    file-path inputs that are not wired via ${{ context.* }} references.

    This is the consumer-side complement to the implicit-handoff rule (which is producer-side).
    Together they form a bidirectional handoff enforcement pair.
    """
    try:
        manifest = load_bundled_manifest()
    except Exception:
        logger.warning(
            "uncaptured-handoff-consumer: failed to load skill_contracts.yaml; skipping"
        )
        return []

    findings: list[RuleFinding] = []

    for step_name, step in ctx.recipe.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue

        producer_skill = resolve_skill_name(step.with_args.get("skill_command", ""))
        if not producer_skill:
            continue

        producer_contract = get_skill_contract(producer_skill, manifest)
        if producer_contract is None:
            continue
        if producer_contract.outputs:
            continue  # has declared outputs — implicit-handoff rule covers this

        # producer declares outputs: [] — check successors for unsatisfied file-path inputs
        for successor_name in ctx.step_graph.get(step_name, set()):
            successor_step = ctx.recipe.steps.get(successor_name)
            if successor_step is None or successor_step.tool not in SKILL_TOOLS:
                continue

            consumer_skill = resolve_skill_name(successor_step.with_args.get("skill_command", ""))
            if not consumer_skill:
                continue

            consumer_contract = get_skill_contract(consumer_skill, manifest)
            if consumer_contract is None:
                continue

            file_path_inputs = [
                inp
                for inp in consumer_contract.inputs
                if inp.type in ("file_path", "directory_path") and not inp.required
            ]
            if not file_path_inputs:
                continue

            skill_cmd = successor_step.with_args.get("skill_command", "")
            unwired = [inp for inp in file_path_inputs if f"context.{inp.name}" not in skill_cmd]
            if not unwired:
                continue  # all file-path inputs wired via context refs

            input_names = ", ".join(inp.name for inp in unwired)
            findings.append(
                RuleFinding(
                    rule="uncaptured-handoff-consumer",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' invokes '{producer_skill}' which declares no "
                        f"structured outputs (outputs: []). Its successor '{successor_name}' "
                        f"('{consumer_skill}') has optional file-path inputs ({input_names}) "
                        f"not provided via ${{{{ context.* }}}} references. If '{producer_skill}' "
                        f"writes files consumed by '{consumer_skill}', add output emission to "
                        f"the skill and a capture: block on this step."
                    ),
                )
            )

    return findings
