"""Semantic gates for issue-wide plan-set authority and per-part consumers."""

from __future__ import annotations

from collections import deque

from autoskillit.core import Severity, extract_skill_name
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._analysis_bfs import bfs_reachable_without_barrier_in_graph
from autoskillit.recipe._skill_helpers import MULTIPART_SKILL_NAMES
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule
from autoskillit.recipe.schema import RecipeStep, stop_step_declares_success

STAGE_OBLIGATION_GATE_DOMINANCE_RULE = "stage-obligation-gate-dominance"
_STAGE_OBLIGATION_CALLABLE = "autoskillit.recipe._cmd_rpc.verify_stage_obligations"


def _multipart_producer_steps(ctx: ValidationContext) -> list[str]:
    return [
        name
        for name, step in ctx.recipe.steps.items()
        if step.tool == "run_skill"
        and extract_skill_name(step.with_args.get("skill_command", "")) in MULTIPART_SKILL_NAMES
        and "plan_parts" in step.capture_list
    ]


@semantic_rule(
    name="plan-set-authority-not-threaded",
    description="Multipart issue walkthroughs require a sealed bind_plan_set authority.",
    severity=Severity.ERROR,
)
def _check_plan_set_authority(ctx: ValidationContext) -> list[RuleFinding]:
    if "issue_url" not in ctx.recipe.ingredients and "issue_number" not in ctx.recipe.ingredients:
        return []
    producers = _multipart_producer_steps(ctx)
    protected_steps = [
        name
        for name, step in ctx.recipe.steps.items()
        if step.tool == "run_skill"
        and extract_skill_name(step.with_args.get("skill_command", ""))
        in {
            "dry-walkthrough",
            "implement-worktree",
            "implement-worktree-no-merge",
            "retry-worktree",
        }
    ]
    if not producers or not protected_steps:
        return []
    binds = [
        step
        for step in ctx.recipe.steps.values()
        if step.tool == "bind_plan_set"
        and "plan_set_authority_path" in step.capture
        and str(step.with_args.get("seal", "true")).strip("'\"").lower() == "true"
    ]
    findings: list[RuleFinding] = []
    if not binds:
        findings.append(
            make_finding(
                rule_name="plan-set-authority-not-threaded",
                step_name=producers[0],
                message="Multipart issue recipes must bind a sealed plan-set authority.",
            )
        )
    for step_name in protected_steps:
        invocation = ctx.binding_projection.for_step(step_name)
        value = invocation.skill_input("plan_set_authority_path") if invocation else None
        optional = ctx.recipe.steps[step_name].optional_context_refs
        if (
            value is not None
            and value.effective_value == "${{ context.plan_set_authority_path }}"
            and "plan_set_authority_path" not in optional
        ):
            continue
        findings.append(
            make_finding(
                rule_name="plan-set-authority-not-threaded",
                step_name=step_name,
                message="plan-set-consuming skill must thread context.plan_set_authority_path.",
            )
        )
    return findings


def _outgoing_targets(step: object, sealed: bool) -> tuple[tuple[str, bool], ...]:
    targets: list[tuple[str, bool]] = []
    seals_on_success = (
        getattr(step, "tool", None) == "bind_plan_set"
        and str(getattr(step, "with_args", {}).get("seal", "")).strip("'\"").lower() == "true"
    )
    for name in (
        "on_success",
        "on_failure",
        "on_context_limit",
        "on_rate_limit",
        "on_exhausted",
        "on_skip",
    ):
        value = getattr(step, name, None)
        if isinstance(value, str):
            targets.append((value, sealed or (seals_on_success and name == "on_success")))
    on_result = getattr(step, "on_result", None)
    if on_result is not None:
        targets.extend(
            (target, sealed or (seals_on_success and verdict == "true"))
            for verdict, target in on_result.routes.items()
        )
        targets.extend(
            (
                condition.route,
                sealed
                or (
                    seals_on_success
                    and condition.when is not None
                    and "result.success" in condition.when
                    and "== true" in condition.when
                ),
            )
            for condition in on_result.conditions
            if condition.route
        )
    return tuple(targets)


@semantic_rule(
    name="plan-set-authority-sealed-path",
    description="Every multipart producer path to a walkthrough must cross a sealed bind.",
    severity=Severity.ERROR,
)
def _check_sealed_plan_set_paths(ctx: ValidationContext) -> list[RuleFinding]:
    if "issue_url" not in ctx.recipe.ingredients and "issue_number" not in ctx.recipe.ingredients:
        return []
    producers = _multipart_producer_steps(ctx)
    producers.extend(
        name
        for name, step in ctx.recipe.steps.items()
        if step.tool == "run_python"
        and str(step.with_args.get("callable", "")).endswith("verify_plan_artifacts")
    )
    findings: list[RuleFinding] = []
    for producer in producers:
        queue = deque(_outgoing_targets(ctx.recipe.steps[producer], False))
        seen: set[tuple[str, bool]] = set()
        while queue:
            step_name, sealed = queue.popleft()
            if (step_name, sealed) in seen or step_name not in ctx.recipe.steps:
                continue
            seen.add((step_name, sealed))
            step = ctx.recipe.steps[step_name]
            if (
                step.tool == "run_skill"
                and extract_skill_name(step.with_args.get("skill_command", ""))
                == "dry-walkthrough"
                and not sealed
            ):
                findings.append(
                    make_finding(
                        rule_name="plan-set-authority-sealed-path",
                        step_name=step_name,
                        message="dry-walkthrough is reachable without a sealed plan-set bind.",
                    )
                )
                continue
            queue.extend(_outgoing_targets(step, sealed))
    return findings


def _is_sealed_bind(step: RecipeStep) -> bool:
    return (
        step.tool == "bind_plan_set"
        and str(step.with_args.get("seal", "true")).strip("'\"").lower() == "true"
    )


def _gate_non_success_targets(step: RecipeStep) -> set[str]:
    targets = {
        target
        for field in ("on_failure", "on_context_limit", "on_rate_limit", "on_exhausted", "on_skip")
        if isinstance(target := getattr(step, field), str)
    }
    route = step.on_result
    if route is None:
        return targets
    for result, target in route.routes.items():
        if result.strip().lower() not in {"true", "success"}:
            targets.add(target)
    for condition in route.conditions:
        when = condition.when or ""
        if "result.success" not in when or "== true" not in when:
            targets.add(condition.route)
    return targets


@semantic_rule(
    name=STAGE_OBLIGATION_GATE_DOMINANCE_RULE,
    description="Sealed publication requirements cannot reach success without verification.",
    severity=Severity.ERROR,
)
def _check_stage_obligation_gate_dominance(ctx: ValidationContext) -> list[RuleFinding]:
    if not any(
        step.tool == "bind_plan_set" and "open_pr" in step.with_args
        for step in ctx.recipe.steps.values()
    ):
        return []

    gates = {
        name
        for name, step in ctx.recipe.steps.items()
        if step.tool == "run_python"
        and step.with_args.get("callable") == _STAGE_OBLIGATION_CALLABLE
    }
    barriers = frozenset(gates)
    success_stops = {
        name for name, step in ctx.recipe.steps.items() if stop_step_declares_success(step)
    }
    findings: list[RuleFinding] = []

    def success_reachable_without_gate(start: str) -> list[str]:
        reachable = bfs_reachable_without_barrier_in_graph(ctx.step_graph, start, barriers)
        return sorted(reachable & success_stops)

    for bind_name, step in ctx.recipe.steps.items():
        if not _is_sealed_bind(step):
            continue
        for stop_name in success_reachable_without_gate(bind_name):
            findings.append(
                make_finding(
                    rule_name=STAGE_OBLIGATION_GATE_DOMINANCE_RULE,
                    step_name=bind_name,
                    message=(
                        f"Sealed bind '{bind_name}' can reach success stop '{stop_name}' "
                        "without crossing a stage-obligation gate."
                    ),
                )
            )

    for gate_name in sorted(gates):
        gate = ctx.recipe.steps[gate_name]
        if gate.skip_when_false:
            findings.append(
                make_finding(
                    rule_name=STAGE_OBLIGATION_GATE_DOMINANCE_RULE,
                    step_name=gate_name,
                    message=(
                        f"Stage-obligation gate '{gate_name}' cannot declare skip_when_false."
                    ),
                )
            )
        for target in sorted(_gate_non_success_targets(gate)):
            for stop_name in success_reachable_without_gate(target):
                findings.append(
                    make_finding(
                        rule_name=STAGE_OBLIGATION_GATE_DOMINANCE_RULE,
                        step_name=gate_name,
                        message=(
                            f"Gate '{gate_name}' has a non-success route that reaches "
                            f"success stop '{stop_name}' without crossing another gate."
                        ),
                    )
                )
    return findings
