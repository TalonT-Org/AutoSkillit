"""Semantic validation rules for pipeline scripts.

Rules detect logical/semantic errors that schema validation cannot catch.
Each rule is a standalone function decorated with ``@semantic_rule(...)``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from enum import StrEnum

from autoskillit.workflow_loader import Workflow


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


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
    check: Callable[[Workflow], list[RuleFinding]]


_RULE_REGISTRY: list[RuleSpec] = []


def semantic_rule(
    name: str,
    description: str,
    severity: Severity = Severity.WARNING,
) -> Callable:
    """Decorator that registers a semantic validation rule."""

    def decorator(
        fn: Callable[[Workflow], list[RuleFinding]],
    ) -> Callable[[Workflow], list[RuleFinding]]:
        _RULE_REGISTRY.append(
            RuleSpec(name=name, description=description, severity=severity, check=fn)
        )
        return fn

    return decorator


def run_semantic_rules(wf: Workflow) -> list[RuleFinding]:
    """Execute all registered semantic rules against a workflow."""
    findings: list[RuleFinding] = []
    for spec in _RULE_REGISTRY:
        findings.extend(spec.check(wf))
    return findings


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

_SKILL_TOOLS = frozenset({"run_skill", "run_skill_retry"})


@semantic_rule(
    name="retry-without-worktree-path",
    description=(
        "run_skill_retry steps with needs_retry routing must receive "
        "worktree_path from a prior capture to resume instead of restarting."
    ),
    severity=Severity.ERROR,
)
def _check_retry_without_worktree_path(wf: Workflow) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    available_context: set[str] = set()

    for step_name, step in wf.steps.items():
        if (
            step.tool == "run_skill_retry"
            and step.retry
            and step.retry.on == "needs_retry"
            and "worktree_path" in available_context
        ):
            has_worktree_ref = any(
                "context.worktree_path" in str(val) for val in step.with_args.values()
            )
            if not has_worktree_ref:
                findings.append(
                    RuleFinding(
                        rule="retry-without-worktree-path",
                        severity=Severity.ERROR,
                        step_name=step_name,
                        message=(
                            f"Step '{step_name}' uses run_skill_retry with needs_retry "
                            f"routing but does not receive worktree_path from context. "
                            f"A preceding step captures worktree_path — add "
                            f"'worktree_path: \"${{{{ context.worktree_path }}}}\"' to "
                            f"this step's 'with:' block so retries resume the existing "
                            f"worktree instead of creating a new one."
                        ),
                    )
                )
        available_context.update(step.capture.keys())

    return findings


@semantic_rule(
    name="unreachable-step",
    description="Steps that no other step routes to (and are not the entry point) are dead code.",
    severity=Severity.WARNING,
)
def _check_unreachable_steps(wf: Workflow) -> list[RuleFinding]:
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
def _check_model_on_non_skill(wf: Workflow) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        if step.model and step.tool not in _SKILL_TOOLS:
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
def _check_retry_without_capture(wf: Workflow) -> list[RuleFinding]:
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
