"""Semantic validation rules — dataflow analysis."""

from __future__ import annotations

from autoskillit.core import PIPELINE_FORBIDDEN_TOOLS, SKILL_TOOLS, Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import (
    RESULT_CAPTURE_RE,
    get_callable_contract,
    get_skill_contract,
    load_bundled_manifest,
    resolve_skill_name,
)
from autoskillit.recipe.registry import RuleFinding, semantic_rule


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
            for ref_key in RESULT_CAPTURE_RE.findall(capture_expr.from_):
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
            for ref_key in RESULT_CAPTURE_RE.findall(capture_expr.from_):
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
            result_refs.extend(RESULT_CAPTURE_RE.findall(capture_expr.from_))
        for _capture_var, capture_expr in step.capture_list.items():
            result_refs.extend(RESULT_CAPTURE_RE.findall(capture_expr.from_))
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
    """Error when a capture variable is never consumed downstream; warning for capture_list."""
    findings: list[RuleFinding] = []
    for w in ctx.dataflow.warnings:
        if w.code != "DEAD_OUTPUT":
            continue
        step = ctx.recipe.steps.get(w.step_name)
        is_capture_list_only = (
            step is not None
            and w.field in (step.capture_list or {})
            and w.field not in (step.capture or {})
        )
        findings.append(
            RuleFinding(
                rule="dead-output",
                severity=Severity.WARNING if is_capture_list_only else Severity.ERROR,
                step_name=w.step_name,
                message=w.message,
            )
        )
    return findings
