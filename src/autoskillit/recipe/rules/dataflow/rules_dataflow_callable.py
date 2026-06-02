"""Semantic rules for callable contract validation and context wiring."""

from __future__ import annotations

import importlib
import inspect

from autoskillit.core import RUN_PYTHON_SENTINEL_KEYS, SKILL_TOOLS, Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import (
    _CONTEXT_REF_RE,
    get_callable_contract,
    load_bundled_manifest,
)
from autoskillit.recipe.registry import RuleFinding, semantic_rule


def _get_provided_args(with_args: dict) -> set[str]:
    _nested = with_args.get("args")
    nested_args: set[str] = set(_nested.keys()) if isinstance(_nested, dict) else set()
    top_level_args = set(with_args.keys()) - {"callable", "timeout", "args"}
    return nested_args | top_level_args


def _get_args_values(with_args: dict) -> dict[str, object]:
    result: dict[str, object] = {}
    _nested = with_args.get("args")
    if isinstance(_nested, dict):
        result.update(_nested)
    for key, val in with_args.items():
        if key not in {"callable", "timeout", "args"}:
            result[key] = val
    return result


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
        provided_args = _get_provided_args(step.with_args)
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
            sig = inspect.signature(func)
        except (ImportError, AttributeError, ValueError):
            continue
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            continue
        valid_params = set(sig.parameters.keys())
        tool_level_params = RUN_PYTHON_SENTINEL_KEYS | {"args", "work_dir"}
        provided_args = _get_provided_args(step.with_args) - tool_level_params
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


@semantic_rule(
    name="downstream-context-gap",
    description=(
        "A step's skill_command references ${{ context.X }} but X is not produced "
        "by any prior step capture, not declared as a recipe input, and not in the "
        "recipe's ambient context. The variable is unreachable."
    ),
    severity=Severity.WARNING,
)
def _check_downstream_context_completeness(ctx: ValidationContext) -> list[RuleFinding]:
    """Check that context variables referenced in skill_command are actually wired.

    For each run_skill step, extract all ${{ context.X }} references from
    skill_command and verify that X is either:
    (a) produced by a prior step's capture: block (result.X wired to context.X), or
    (b) declared as a top-level recipe input.
    """
    findings: list[RuleFinding] = []

    produced_context: set[str] = set()
    recipe_inputs: set[str] = (
        set(ctx.recipe.ingredients.keys()) if ctx.recipe.ingredients else set()
    )

    ordered_steps = list(ctx.recipe.steps.keys())

    for step_name in ordered_steps:
        step = ctx.recipe.steps[step_name]

        # Check references first so a step's own captures cannot satisfy its own refs.
        # Captures become available to *subsequent* steps, not to the current step.
        if step.tool in SKILL_TOOLS:
            skill_cmd = (step.with_args or {}).get("skill_command", "")
            if isinstance(skill_cmd, str):
                for ref in _CONTEXT_REF_RE.findall(skill_cmd):
                    if ref in produced_context or ref in recipe_inputs:
                        continue
                    findings.append(
                        RuleFinding(
                            rule="downstream-context-gap",
                            severity=Severity.WARNING,
                            step_name=step_name,
                            message=(
                                f"Step '{step_name}' references ${{{{ context.{ref}}}}} in its "
                                f"skill_command but '{ref}' is not produced by any prior step's "
                                f"capture block and is not a recipe input."
                                " The variable is unreachable."
                            ),
                        )
                    )

        if step.capture or step.capture_list:
            for ctx_var in step.capture or {}:
                produced_context.add(ctx_var)
            for ctx_var in step.capture_list or {}:
                produced_context.add(ctx_var)

    return findings


@semantic_rule(
    name="nullable-optional-context-ref",
    description=(
        "run_python steps must not pass optional_context_refs values to non-nullable "
        "callable inputs without null coercion in the callable"
    ),
    severity=Severity.ERROR,
)
def _check_nullable_optional_context_ref(ctx: ValidationContext) -> list[RuleFinding]:
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
        non_nullable = {inp.name for inp in contract.inputs if not inp.nullable}
        if not non_nullable:
            continue
        optional_refs = set(step.optional_context_refs)
        if not optional_refs:
            continue
        args_values = _get_args_values(step.with_args)
        for inp_name in sorted(non_nullable):
            arg_val = args_values.get(inp_name)
            if not isinstance(arg_val, str):
                continue
            for ref in _CONTEXT_REF_RE.findall(arg_val):
                if ref in optional_refs:
                    findings.append(
                        RuleFinding(
                            rule="nullable-optional-context-ref",
                            severity=Severity.ERROR,
                            step_name=step_name,
                            message=(
                                f"Step '{step_name}' passes optional context ref '{ref}' "
                                f"to non-nullable input '{inp_name}' of callable "
                                f"'{callable_path}'. Add null coercion in the callable "
                                f"or remove from optional_context_refs."
                            ),
                        )
                    )
    return findings


@semantic_rule(
    name="work-dir-arg-misplacement",
    description=(
        "run_python step has work_dir inside args but the callable does not accept "
        "a work_dir parameter — likely intended as a top-level tool parameter for "
        "path anchoring"
    ),
    severity=Severity.WARNING,
)
def _check_work_dir_arg_misplacement(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_python":
            continue
        nested = step.with_args.get("args")
        if not isinstance(nested, dict) or "work_dir" not in nested:
            continue
        callable_path = step.with_args.get("callable", "")
        if not callable_path:
            continue
        try:
            module_path, attr_name = callable_path.rsplit(".", 1)
            mod = importlib.import_module(module_path)
            func = getattr(mod, attr_name)
            sig = inspect.signature(func)
        except (ImportError, AttributeError, ValueError):
            continue
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            continue
        if "work_dir" not in sig.parameters:
            findings.append(
                RuleFinding(
                    rule="work-dir-arg-misplacement",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' has work_dir inside args but "
                        f"'{callable_path}' does not accept a work_dir parameter. "
                        f"Move work_dir to the top-level with: block for path anchoring."
                    ),
                )
            )
    return findings
