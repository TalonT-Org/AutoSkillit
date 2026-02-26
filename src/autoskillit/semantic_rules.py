"""Semantic validation rules for pipeline scripts.

Rules detect logical/semantic errors that schema validation cannot catch.
Each rule is a standalone function decorated with ``@semantic_rule(...)``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable

from autoskillit.recipe_parser import Recipe, iter_steps_with_context
from autoskillit.types import PIPELINE_FORBIDDEN_TOOLS, SKILL_TOOLS, Severity


@dataclasses.dataclass
class RuleFinding:
    """A single finding produced by a semantic rule."""

    rule: str
    severity: Severity
    step_name: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "rule": self.rule,
            "severity": self.severity.value,
            "step": self.step_name,
            "message": self.message,
        }


@dataclasses.dataclass
class RuleSpec:
    """Internal: metadata for one registered rule."""

    name: str
    description: str
    severity: Severity
    check: Callable[[Recipe], list[RuleFinding]]


_RULE_REGISTRY: list[RuleSpec] = []


def semantic_rule(
    name: str,
    description: str,
    severity: Severity = Severity.WARNING,
) -> Callable:
    """Decorator that registers a semantic validation rule."""

    def decorator(
        fn: Callable[[Recipe], list[RuleFinding]],
    ) -> Callable[[Recipe], list[RuleFinding]]:
        _RULE_REGISTRY.append(
            RuleSpec(name=name, description=description, severity=severity, check=fn)
        )
        return fn

    return decorator


def run_semantic_rules(wf: Recipe) -> list[RuleFinding]:
    """Execute all registered semantic rules against a workflow."""
    findings: list[RuleFinding] = []
    for spec in _RULE_REGISTRY:
        findings.extend(spec.check(wf))
    return findings


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


@semantic_rule(
    name="outdated-recipe-version",
    description="Recipe's autoskillit_version is below the installed package version",
    severity=Severity.WARNING,
)
def _check_outdated_version(wf: Recipe) -> list[RuleFinding]:
    from packaging.version import Version

    from autoskillit import __version__

    script_ver = wf.version
    if script_ver is None:
        return [
            RuleFinding(
                rule="outdated-recipe-version",
                severity=Severity.WARNING,
                step_name="(top-level)",
                message=(
                    f"Recipe has no autoskillit_version field. "
                    f"Current installed version is {__version__}. "
                    f"Run 'autoskillit migrate' to update."
                ),
            )
        ]

    if Version(script_ver) < Version(__version__):
        return [
            RuleFinding(
                rule="outdated-recipe-version",
                severity=Severity.WARNING,
                step_name="(top-level)",
                message=(
                    f"Recipe version {script_ver} is behind installed "
                    f"version {__version__}. Run 'autoskillit migrate' to update."
                ),
            )
        ]

    return []


@semantic_rule(
    name="missing-ingredient",
    description=(
        "Skill steps must provide all required ingredients via context or recipe "
        "ingredient references. Detects when a skill requires an ingredient that the "
        "step does not reference."
    ),
    severity=Severity.ERROR,
)
def _check_unsatisfied_skill_input(wf: Recipe) -> list[RuleFinding]:
    from autoskillit.contract_validator import (
        count_positional_args,
        extract_context_refs,
        extract_input_refs,
        get_skill_contract,
        load_bundled_manifest,
        resolve_skill_name,
    )

    findings: list[RuleFinding] = []
    manifest = load_bundled_manifest()
    ingredient_names = set(wf.ingredients.keys())

    for step_name, step, available_context in iter_steps_with_context(wf):
        if step.tool not in SKILL_TOOLS:
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        skill_name = resolve_skill_name(skill_cmd)
        if not skill_name:
            continue
        contract = get_skill_contract(skill_name, manifest)
        if not contract:
            continue
        if count_positional_args(skill_cmd) > 0:
            continue

        ctx_refs = extract_context_refs(step)
        inp_refs = extract_input_refs(step)
        provided = ctx_refs | inp_refs

        for req_input in contract.inputs:
            if not req_input.required:
                continue
            name = req_input.name
            if name not in provided:
                if name in available_context or name in ingredient_names:
                    msg = (
                        f"Step '{step_name}' invokes {skill_name} which requires "
                        f"'{name}', and '{name}' is available in the recipe "
                        f"context, but the step does not reference it. Add "
                        f"'${{{{ context.{name} }}}}' to the step's skill_command "
                        f"or with: block."
                    )
                else:
                    msg = (
                        f"Step '{step_name}' invokes {skill_name} which requires "
                        f"'{name}', but '{name}' is not available at this point "
                        f"in the recipe. No prior step captures it and it is "
                        f"not a recipe ingredient."
                    )
                findings.append(
                    RuleFinding(
                        rule="missing-ingredient",
                        severity=Severity.ERROR,
                        step_name=step_name,
                        message=msg,
                    )
                )

    return findings


@semantic_rule(
    name="unreachable-step",
    description="Steps that no other step routes to (and are not the entry point) are dead code.",
    severity=Severity.WARNING,
)
def _check_unreachable_steps(wf: Recipe) -> list[RuleFinding]:
    if not wf.steps:
        return []

    referenced: set[str] = set()
    for step in wf.steps.values():
        if step.on_success:
            referenced.add(step.on_success)
        if step.on_failure:
            referenced.add(step.on_failure)
        if step.on_result:
            referenced.update(step.on_result.routes.values())
        if step.retry and step.retry.on_exhausted:
            referenced.add(step.retry.on_exhausted)
    referenced.discard("done")

    first_step = next(iter(wf.steps))
    findings: list[RuleFinding] = []
    for step_name in wf.steps:
        if step_name != first_step and step_name not in referenced:
            findings.append(
                RuleFinding(
                    rule="unreachable-step",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' is not the entry point and no other step "
                        f"routes to it. It will never execute. Remove it or add routing."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="model-on-non-skill-step",
    description="The 'model' field only affects run_skill/run_skill_retry steps.",
    severity=Severity.WARNING,
)
def _check_model_on_non_skill(wf: Recipe) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        if step.model and step.tool not in SKILL_TOOLS:
            findings.append(
                RuleFinding(
                    rule="model-on-non-skill-step",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' has 'model: {step.model}' but uses "
                        f"tool '{step.tool}'. The model field only affects "
                        f"run_skill and run_skill_retry. Remove it to avoid confusion."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="retry-without-capture",
    description=(
        "run_skill_retry steps with retry routing that feed downstream "
        "context references must have capture blocks to supply those values."
    ),
    severity=Severity.WARNING,
)
def _check_retry_without_capture(wf: Recipe) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    step_names = list(wf.steps.keys())

    for idx, (step_name, step) in enumerate(wf.steps.items()):
        if step.tool == "run_skill_retry" and step.retry and not step.capture:
            downstream_needs_context = False
            for later_name in step_names[idx + 1 :]:
                later_step = wf.steps[later_name]
                for val in later_step.with_args.values():
                    if "context." in str(val):
                        downstream_needs_context = True
                        break
                if downstream_needs_context:
                    break

            if downstream_needs_context:
                findings.append(
                    RuleFinding(
                        rule="retry-without-capture",
                        severity=Severity.WARNING,
                        step_name=step_name,
                        message=(
                            f"Step '{step_name}' uses run_skill_retry with retry "
                            f"routing but has no capture block. A downstream step "
                            f"references context values — add a capture block to "
                            f"thread outputs (e.g., worktree_path, plan_path) forward."
                        ),
                    )
                )
    return findings


_WORKTREE_CREATING_SKILLS = frozenset(
    {
        "implement-worktree",
        "implement-worktree-no-merge",
    }
)


@semantic_rule(
    name="worktree-retry-creates-new",
    description=(
        "Worktree-creating skills (implement-worktree, "
        "implement-worktree-no-merge) must not have retry "
        "max_attempts > 1. Each retry re-invokes the skill, "
        "creating a new worktree and orphaning the previous one. "
        "Use max_attempts: 1 and route on_exhausted to a "
        "retry-worktree step instead."
    ),
    severity=Severity.ERROR,
)
def _check_worktree_retry_creates_new(
    wf: Recipe,
) -> list[RuleFinding]:
    from autoskillit.contract_validator import resolve_skill_name

    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        if not step.retry or step.retry.max_attempts <= 1:
            continue

        skill_cmd = step.with_args.get("skill_command", "")
        skill_name = resolve_skill_name(skill_cmd)
        if skill_name and skill_name in _WORKTREE_CREATING_SKILLS:
            findings.append(
                RuleFinding(
                    rule="worktree-retry-creates-new",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' retries {skill_name} "
                        f"with max_attempts="
                        f"{step.retry.max_attempts}. Each retry "
                        f"creates a new worktree, orphaning partial "
                        f"progress. Set max_attempts: 1 and route "
                        f"on_exhausted to a retry-worktree step that "
                        f"resumes in the existing worktree."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="weak-constraint-text",
    description=(
        "Pipeline constraints should enumerate forbidden native tools by name. "
        "Generic one-liner constraints like 'Only use MCP tools' are too vague "
        "to enforce orchestrator discipline."
    ),
    severity=Severity.WARNING,
)
def _check_weak_constraint_text(wf: Recipe) -> list[RuleFinding]:
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
