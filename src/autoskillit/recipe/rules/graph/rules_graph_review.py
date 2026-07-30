"""Semantic rules for pass-through validity, review waypoint guards, and context limit."""

from __future__ import annotations

import regex as re

from autoskillit.core import SKILL_TOOLS, Severity, get_logger, resolve_skill_name
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._analysis_bfs import bfs_reachable_without_barrier
from autoskillit.recipe.contracts import load_bundled_manifest
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule

logger = get_logger(__name__)


@semantic_rule(
    name="pass-through-validity",
    description=(
        "A step's pass_through list must only reference outputs that are actually "
        "captured by the step, and must not reference outputs used in on_result "
        "when clauses (which indicates the output controls routing)."
    ),
    severity=Severity.WARNING,
)
def _check_pass_through_validity(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    try:
        manifest = load_bundled_manifest()
    except (FileNotFoundError, OSError, ValueError):
        logger.warning("failed to load bundled manifest", exc_info=True)
        return findings
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        if not step.pass_through:
            continue
        skill_command = (step.with_args or {}).get("skill_command", "")
        if not isinstance(skill_command, str):
            continue
        skill_name = resolve_skill_name(skill_command)
        if not skill_name:
            continue
        skill_contract = manifest.get("skills", {}).get(skill_name, {})
        all_output_names: set[str] = set()
        outputs_with_allowed_values: dict[str, list[str]] = {}
        for output in skill_contract.get("outputs", []):
            all_output_names.add(output["name"])
            if "allowed_values" in output:
                outputs_with_allowed_values[output["name"]] = output["allowed_values"]
        captured_outputs: set[str] = set()
        if step.capture:
            for captured_var, capture_expr in step.capture.items():
                for output_name in all_output_names:
                    if f"result.{output_name}" in capture_expr.from_:
                        captured_outputs.add(output_name)
        used_in_when: set[str] = set()
        if step.on_result and step.on_result.conditions:
            for cond in step.on_result.conditions:
                if cond.when:
                    for output_name in all_output_names:
                        if f"result.{output_name}" in cond.when:
                            used_in_when.add(output_name)
        for pt_name in step.pass_through:
            if pt_name not in captured_outputs:
                findings.append(
                    make_finding(
                        rule_name="pass-through-validity",
                        step_name=step_name,
                        message=f"pass_through references '{pt_name}' but this output "
                        f"is not captured by step '{step_name}'.",
                    )
                )
            elif pt_name in used_in_when:
                findings.append(
                    make_finding(
                        rule_name="pass-through-validity",
                        step_name=step_name,
                        message=f"pass_through references '{pt_name}' but this output "
                        f"is used in a when clause of step '{step_name}' on_result, "
                        f"indicating it controls routing.",
                    )
                )
    return findings


@semantic_rule(
    name="review-loop-waypoint-guard",
    description=(
        "check_repo_ci_event must not be reachable from review_pr without "
        "traversing check_review_loop. Bypassing check_review_loop prevents "
        "review_loop_count from incrementing, causing review_mode to remain "
        "'local' permanently and no GitHub comments to be posted on clean PRs."
    ),
    severity=Severity.ERROR,
)
def _check_review_loop_waypoint(ctx: ValidationContext) -> list[RuleFinding]:
    steps = ctx.recipe.steps
    if not all(k in steps for k in ("review_pr", "check_review_loop", "check_repo_ci_event")):
        return []

    reachable = bfs_reachable_without_barrier(
        recipe=ctx.recipe,
        start="review_pr",
        barrier="check_review_loop",
    )

    if "check_repo_ci_event" not in reachable:
        return []

    return [
        make_finding(
            rule_name="review-loop-waypoint-guard",
            step_name="review_pr",
            message="check_repo_ci_event is reachable from review_pr without crossing "
            "check_review_loop. All review_pr verdicts must route through "
            "check_review_loop so review_loop_count is always incremented and "
            "review_mode can graduate from 'local' to 'github'.",
        )
    ]


@semantic_rule(
    name="run-skill-missing-context-limit",
    description=(
        "All run_skill steps must declare on_context_limit. "
        "When context is exhausted mid-execution, the orchestrator needs a "
        "deterministic recovery path. Without it, on_failure is used as the "
        "fallback — discarding all uncommitted edits and losing partial progress."
    ),
    severity=Severity.WARNING,
)
def _check_run_skill_missing_context_limit(ctx: ValidationContext) -> list[RuleFinding]:
    recipe = ctx.recipe
    findings: list[RuleFinding] = []

    # Steps that are themselves on_context_limit targets are exempt — they ARE
    # the recovery path and do not need to declare a recovery of their own.
    context_limit_targets: set[str] = set()
    for step in recipe.steps.values():
        if step.on_context_limit and step.on_context_limit not in (
            "escalate",
            "release_issue_failure",
        ):
            context_limit_targets.add(step.on_context_limit)

    for step_name, step in recipe.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        if step.action == "stop":
            continue
        if step.on_context_limit is not None:
            continue
        if step_name in context_limit_targets:
            continue
        findings.append(
            make_finding(
                rule_name="run-skill-missing-context-limit",
                step_name=step_name,
                message=f"Step '{step_name}' ({step.tool}) has no on_context_limit. "
                f"If context is exhausted mid-execution, on_failure is used as "
                f"fallback — discarding uncommitted edits and losing partial progress. "
                f"Add on_context_limit: <recovery_step>.",
            )
        )
    return findings


@semantic_rule(
    name="run-skill-missing-rate-limit",
    description=(
        "All run_skill steps must declare on_rate_limit. "
        "When a transient HTTP 429 rate limit occurs, the orchestrator needs a "
        "deterministic recovery path. Without it, on_context_limit is borrowed — "
        "conflating transient throttling with structural context exhaustion."
    ),
    severity=Severity.WARNING,
)
def _check_run_skill_missing_rate_limit(ctx: ValidationContext) -> list[RuleFinding]:
    recipe = ctx.recipe
    findings: list[RuleFinding] = []

    # Steps that are themselves on_rate_limit targets are exempt — they ARE
    # the recovery path and do not need to declare a recovery of their own.
    rate_limit_targets: set[str] = set()
    for step in recipe.steps.values():
        if step.on_rate_limit and step.on_rate_limit not in (
            "escalate",
            "release_issue_failure",
        ):
            rate_limit_targets.add(step.on_rate_limit)

    for step_name, step in recipe.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        if step.action == "stop":
            continue
        if step.on_rate_limit is not None:
            continue
        if step_name in rate_limit_targets:
            continue
        findings.append(
            make_finding(
                rule_name="run-skill-missing-rate-limit",
                step_name=step_name,
                message=f"Step '{step_name}' ({step.tool}) has no on_rate_limit. "
                f"Transient HTTP 429 rate limits will borrow on_context_limit routing — "
                f"conflating transient throttling with structural context exhaustion. "
                f"Add on_rate_limit: <recovery_step>.",
            )
        )
    return findings


_REVIEW_EFFECT_ADVANCE_GATES = frozenset({"check_review_loop", "derive_batch_ci_event"})
_STALE_SNAPSHOT_WHEN_RE = re.compile(
    r"(?:\$\{\{\s*)?result\.verdict(?:\s*\}\})?\s*==\s*"
    r"""(?:"stale_snapshot"|'stale_snapshot'|stale_snapshot)"""
)


def _stale_snapshot_bypass_edges(
    ctx: ValidationContext,
    start: str,
) -> frozenset[tuple[str, str]]:
    """Return the proved no-effect review-loop edge exempt from receipt checking."""
    step = ctx.recipe.steps[start]
    if step.on_result is None:
        return frozenset()
    loop_conditions = [
        condition
        for condition in step.on_result.conditions
        if condition.route == "check_review_loop"
    ]
    if not loop_conditions or not all(
        condition.when is not None and _STALE_SNAPSHOT_WHEN_RE.fullmatch(condition.when.strip())
        for condition in loop_conditions
    ):
        return frozenset()
    return frozenset({(start, "check_review_loop")})


def _get_review_pr_steps(ctx: ValidationContext) -> list[str]:
    """Return step IDs that dispatch the review-pr skill (either prefix form).

    Exposed as a module-level function (not inlined in the rule body) so that
    silence tests can call it directly and assert the rule is triggered on the
    recipe under test before asserting zero findings.
    """
    return [
        step_id
        for step_id, step in ctx.recipe.steps.items()
        if step.tool == "run_skill" and step.skill_name == "review-pr"
    ]


@semantic_rule(
    name="review-effect-verification-waypoint",
    severity=Severity.ERROR,
    description=(
        "No advance gate (check_review_loop or derive_batch_ci_event) must be reachable "
        "from any review-pr skill step on the success path without first crossing "
        "check_review_posted, except an exact stale_snapshot route to check_review_loop "
        "because that verdict publishes no review effect. Applies to all steps dispatching "
        "the review-pr skill regardless of step id (covers review_pr_integration in "
        "merge-prs.yaml)."
    ),
)
def _review_effect_verification_waypoint(ctx: ValidationContext) -> list[RuleFinding]:
    effect_steps = _get_review_pr_steps(ctx)
    if not effect_steps:
        return []
    findings = []
    for start in effect_steps:
        reachable = bfs_reachable_without_barrier(
            ctx.recipe,
            start=start,
            barrier=frozenset({"check_review_posted"}),
            ignored_edges=_stale_snapshot_bypass_edges(ctx, start),
        )
        for advance_gate in _REVIEW_EFFECT_ADVANCE_GATES:
            if advance_gate in reachable:
                findings.append(
                    make_finding(
                        rule_name="review-effect-verification-waypoint",
                        step_name=start,
                        message=(
                            f"Step '{start}' (review-pr skill) can reach '{advance_gate}' "
                            f"without crossing check_review_posted. Insert a check_review_posted "
                            f"run_python step on every success path from '{start}' to "
                            f"'{advance_gate}'."
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="review-mode-reentry-waypoint-guard",
    description=(
        "review_pr must not be reachable from check_review_loop without "
        "traversing annotate_pr_diff. Bypassing annotate_pr_diff prevents "
        "review_mode from being recomputed on loop re-entry, causing mode "
        "to remain 'local' permanently and no GitHub comments to be posted."
    ),
    severity=Severity.ERROR,
)
def _check_review_mode_reentry_waypoint(ctx: ValidationContext) -> list[RuleFinding]:
    steps = ctx.recipe.steps
    if not all(k in steps for k in ("review_pr", "check_review_loop", "annotate_pr_diff")):
        return []

    reachable = bfs_reachable_without_barrier(
        recipe=ctx.recipe,
        start="check_review_loop",
        barrier="annotate_pr_diff",
    )

    if "review_pr" not in reachable:
        return []

    return [
        make_finding(
            rule_name="review-mode-reentry-waypoint-guard",
            step_name="check_review_loop",
            message="review_pr is reachable from check_review_loop without crossing "
            "annotate_pr_diff. All loop re-entry paths must traverse "
            "annotate_pr_diff so review_mode is recomputed with the updated "
            "review_loop_count on every iteration.",
        )
    ]
