"""Semantic validation rules — dataflow analysis."""

from __future__ import annotations

import importlib
import inspect

from autoskillit.core import (
    PIPELINE_FORBIDDEN_TOOLS,
    SKILL_TOOLS,
    Severity,
    get_logger,
)
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import (
    RESULT_CAPTURE_RE,
    get_callable_contract,
    get_skill_contract,
    load_bundled_manifest,
    resolve_skill_name,
)
from autoskillit.recipe.registry import RuleFinding, semantic_rule

logger = get_logger(__name__)


@semantic_rule(
    name="weak-constraint-text",
    description="Pipeline constraints must enumerate forbidden native tools by name.",
    severity=Severity.WARNING,
)
def _check_weak_constraint_text(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    if not wf.kitchen_rules:
        return []

    all_text = " ".join(wf.kitchen_rules)
    found = sum(1 for tool in PIPELINE_FORBIDDEN_TOOLS if tool in all_text)
    if found < len(PIPELINE_FORBIDDEN_TOOLS):
        tool_list = ", ".join(PIPELINE_FORBIDDEN_TOOLS)
        return [
            RuleFinding(
                rule="weak-constraint-text",
                severity=Severity.WARNING,
                step_name="(recipe)",
                message=(
                    "Recipe kitchen_rules do not enumerate forbidden native tools. "
                    f"Name specific tools ({tool_list}) "
                    "for orchestrator discipline."
                ),
            )
        ]
    return []


@semantic_rule(
    name="undeclared-capture-key",
    description="result.X captures must match skill output keys in skill_contracts.yaml",
    severity=Severity.ERROR,
)
def _check_capture_output_coverage(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings: list[RuleFinding] = []
    manifest = load_bundled_manifest()

    for step_name, step in wf.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        if not step.capture and not step.capture_list:
            continue

        skill_cmd = step.with_args.get("skill_command", "")
        skill_name = resolve_skill_name(skill_cmd)
        if not skill_name:
            # Dynamic or non-autoskillit skill_command — cannot validate.
            continue

        contract = get_skill_contract(skill_name, manifest)
        if contract is None:
            findings.append(
                RuleFinding(
                    rule="undeclared-capture-key",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' captures from skill '{skill_name}' "
                        f"which has no outputs contract entry in skill_contracts.yaml. "
                        f"Add an outputs section to verify capture correctness."
                    ),
                )
            )
            continue

        declared_keys = {out.name for out in contract.outputs}

        for _capture_var, capture_expr in step.capture.items():
            for ref_key in RESULT_CAPTURE_RE.findall(capture_expr):
                if ref_key not in declared_keys:
                    findings.append(
                        RuleFinding(
                            rule="undeclared-capture-key",
                            severity=Severity.ERROR,
                            step_name=step_name,
                            message=(
                                f"Step '{step_name}' captures result.{ref_key} "
                                f"but skill '{skill_name}' does not declare '{ref_key}' "
                                f"in its outputs contract."
                            ),
                        )
                    )

        for _capture_var, capture_expr in step.capture_list.items():
            for ref_key in RESULT_CAPTURE_RE.findall(capture_expr):
                if ref_key not in declared_keys:
                    findings.append(
                        RuleFinding(
                            rule="undeclared-capture-key",
                            severity=Severity.ERROR,
                            step_name=step_name,
                            message=(
                                f"Step '{step_name}' captures result.{ref_key} via capture_list "
                                f"but skill '{skill_name}' does not declare '{ref_key}' "
                                f"in its outputs contract."
                            ),
                        )
                    )

    return findings


@semantic_rule(
    name="undeclared-python-capture-key",
    description="result.X references in run_python steps must match callable contract outputs",
    severity=Severity.WARNING,
)
def _check_python_capture_output_coverage(ctx: ValidationContext) -> list[RuleFinding]:
    """Validate that run_python steps only reference declared callable output fields.

    Checks both capture: mappings and on_result condition when: expressions
    for result.* field references, and verifies each against the callable's
    contract in callable_contracts section of skill_contracts.yaml.
    """
    wf = ctx.recipe
    findings: list[RuleFinding] = []
    manifest = load_bundled_manifest()

    for step_name, step in wf.steps.items():
        if step.tool != "run_python":
            continue

        callable_path = step.with_args.get("callable", "")
        if not callable_path:
            continue

        # Collect all result.* references from capture, capture_list, and on_result
        result_refs: list[str] = []
        for _capture_var, capture_expr in step.capture.items():
            result_refs.extend(RESULT_CAPTURE_RE.findall(capture_expr))
        for _capture_var, capture_expr in step.capture_list.items():
            result_refs.extend(RESULT_CAPTURE_RE.findall(capture_expr))
        if step.on_result is not None:
            for cond in step.on_result.conditions:
                if cond.when is not None:
                    result_refs.extend(RESULT_CAPTURE_RE.findall(cond.when))

        if not result_refs:
            continue

        contract = get_callable_contract(callable_path, manifest)
        if contract is None:
            findings.append(
                RuleFinding(
                    rule="undeclared-python-capture-key",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' references result.* fields from callable "
                        f"'{callable_path}' which has no callable contract entry in "
                        f"skill_contracts.yaml. Add a callable_contracts section to "
                        f"verify capture correctness."
                    ),
                )
            )
            continue

        declared_keys = {out.name for out in contract.outputs}
        for ref_key in result_refs:
            if ref_key not in declared_keys:
                findings.append(
                    RuleFinding(
                        rule="undeclared-python-capture-key",
                        severity=Severity.WARNING,
                        step_name=step_name,
                        message=(
                            f"Step '{step_name}' references result.{ref_key} "
                            f"but callable '{callable_path}' does not declare "
                            f"'{ref_key}' in its outputs contract."
                        ),
                    )
                )

    return findings


@semantic_rule(
    name="dead-output",
    description="Captured variable never consumed downstream",
    severity=Severity.ERROR,
)
def _check_dead_output(ctx: ValidationContext) -> list[RuleFinding]:
    """Error when any captured context variable is never consumed downstream."""
    return [
        RuleFinding(
            rule="dead-output",
            severity=Severity.ERROR,
            step_name=w.step_name,
            message=w.message,
        )
        for w in ctx.dataflow.warnings
        if w.code == "DEAD_OUTPUT"
    ]


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
    name="multipart-iteration-notes",
    description="Multi-part plan recipes must declare iteration conventions.",
    severity=Severity.ERROR,
)
def _check_multipart_iteration_notes(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    _MULTIPART_SKILLS = {"/autoskillit:make-plan", "/autoskillit:rectify"}
    findings: list[RuleFinding] = []

    has_multipart_step = any(
        step.tool in SKILL_TOOLS
        and any(s in step.with_args.get("skill_command", "") for s in _MULTIPART_SKILLS)
        for step in wf.steps.values()
    )
    if not has_multipart_step:
        return []

    plan_step = wf.steps.get("plan")
    plan_note = (plan_step.note or "") if plan_step is not None else ""
    # Also accept the glob pattern from the note of whichever step invokes the multipart skill
    multipart_step_notes = [
        (step.note or "")
        for step in wf.steps.values()
        if step.tool in SKILL_TOOLS
        and any(s in step.with_args.get("skill_command", "") for s in _MULTIPART_SKILLS)
    ]
    if "*_part_*.md" not in plan_note and not any(
        "*_part_*.md" in note for note in multipart_step_notes
    ):
        findings.append(
            RuleFinding(
                rule="multipart-glob-note",
                severity=Severity.ERROR,
                step_name="plan",
                message=(
                    "Recipe uses make-plan or rectify but neither the 'plan' step note nor "
                    "the planning step's own note contains '*_part_*.md'. Agents will not "
                    "know to glob for multi-part plan files. Add: "
                    "'Glob plan_dir for *_part_*.md or single plan file.' to the plan "
                    "step's note (or to the make-plan/rectify step's note if no separate "
                    "'plan' step exists)."
                ),
            )
        )

    sequential_keywords = ("SEQUENTIAL EXECUTION", "full cycle", "Never run verify for all parts")
    rules_text = " ".join(wf.kitchen_rules)
    if not any(kw in rules_text for kw in sequential_keywords):
        findings.append(
            RuleFinding(
                rule="multipart-sequential-kitchen-rule",
                severity=Severity.WARNING,
                step_name="kitchen_rules",
                message=(
                    "Recipe uses make-plan or rectify but kitchen_rules do not contain "
                    "a sequential execution constraint. Without it, agents may "
                    "batch-verify all parts before "
                    "implementing any. Add a rule such as: 'SEQUENTIAL EXECUTION: complete full "
                    "cycle per part before advancing.'"
                ),
            )
        )

    next_or_done = wf.steps.get("next_or_done")
    if next_or_done is not None and next_or_done.on_result is not None:
        # Legacy format: field/routes dict with explicit "more_parts" → "verify"
        has_more_parts_to_verify = next_or_done.on_result.routes.get("more_parts") == "verify"
        # Predicate format: condition with "more_parts" in the when clause routing to "verify"
        if not has_more_parts_to_verify:
            has_more_parts_to_verify = any(
                cond.route == "verify" and cond.when is not None and "more_parts" in cond.when
                for cond in next_or_done.on_result.conditions
            )
        if not has_more_parts_to_verify:
            findings.append(
                RuleFinding(
                    rule="multipart-route-back",
                    severity=Severity.ERROR,
                    step_name="next_or_done",
                    message=(
                        "Recipe uses make-plan or rectify but next_or_done does not route "
                        "'more_parts' back to 'verify'. Sequential part processing requires "
                        "more_parts → verify in the on_result routes."
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
        captures_cleanup = any("result.cleanup_succeeded" in str(v) for v in step.capture.values())
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


@semantic_rule(
    name="missing-callable-input",
    description="run_python steps must pass all required inputs declared in callable contract",
    severity=Severity.ERROR,
)
def _check_missing_callable_input(ctx: ValidationContext) -> list[RuleFinding]:
    findings = []
    manifest = load_bundled_manifest()
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_python":
            continue
        callable_path = step.with_args.get("callable", "")
        if not callable_path:
            continue
        contract = get_callable_contract(callable_path, manifest)
        if contract is None:
            continue
        required_inputs = {inp.name for inp in contract.inputs if inp.required}
        _args = step.with_args.get("args")
        provided_args: set[str] = set(_args.keys()) if isinstance(_args, dict) else set()
        missing = required_inputs - provided_args
        for arg_name in sorted(missing):
            findings.append(
                RuleFinding(
                    rule="missing-callable-input",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' calls '{callable_path}' but does not pass "
                        f"required input '{arg_name}'. Add it to the step's args block."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="callable-signature-mismatch",
    description="run_python step args keys must match the callable's function signature",
    severity=Severity.ERROR,
)
def _check_callable_signature_mismatch(ctx: ValidationContext) -> list[RuleFinding]:
    findings = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_python":
            continue
        callable_path = step.with_args.get("callable", "")
        if not callable_path:
            continue
        try:
            module_path, attr_name = callable_path.rsplit(".", 1)
            mod = importlib.import_module(module_path)
            func = getattr(mod, attr_name)
        except (ImportError, AttributeError, ValueError):
            continue
        sig = inspect.signature(func)
        valid_params = set(sig.parameters.keys())
        _args = step.with_args.get("args")
        provided_args: set[str] = set(_args.keys()) if isinstance(_args, dict) else set()
        invalid = provided_args - valid_params
        for arg_name in sorted(invalid):
            findings.append(
                RuleFinding(
                    rule="callable-signature-mismatch",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' passes arg '{arg_name}' to '{callable_path}' "
                        f"but the callable does not accept that parameter."
                    ),
                )
            )
    return findings
