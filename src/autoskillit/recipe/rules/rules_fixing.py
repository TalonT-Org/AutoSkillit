"""Semantic rule: conditional-write skill steps must gate on a declared verdict output.

A step invoking a write_behavior='conditional' skill must not reach a push_to_remote
tool step without passing through an on_result: edge that dispatches on a declared
output field from the skill contract.

This rule closes the no-op loop: if a fix skill emits zero writes (no real fix) its
on_result: must dispatch on a typed verdict, preventing an unconditional re-push from
looping CI indefinitely.

Modelled on the rebase-then-push-requires-force rule in rules_tools.py.
"""

from __future__ import annotations

import re
from collections import deque

from autoskillit.core import SKILL_TOOLS, Severity, get_logger
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import load_bundled_manifest, resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, semantic_rule

logger = get_logger(__name__)

# Maximum hops to walk forward from a conditional-write step when looking for push.
_MAX_HOPS = 6

# Regex to find result.<field> references in a when: expression.
_RESULT_REF_RE = re.compile(r"result\.([\w-]+)")


def _get_skill_outputs(skill_data: dict) -> list[dict]:
    """Return the outputs list from raw skill YAML data."""
    return skill_data.get("outputs", [])


def _declared_output_names(skill_data: dict) -> frozenset[str]:
    """Return the set of declared output field names for a skill."""
    return frozenset(o["name"] for o in _get_skill_outputs(skill_data) if "name" in o)


def _step_gated_on_declared_output(step, declared_outputs: frozenset[str]) -> bool:
    """Return True if this step uses on_result: with a condition referencing a declared output.

    A condition counts as a real gate if:
    - It has a non-None when: expression (not a catch-all)
    - The when: expression references result.<output_name> for some declared output name
    """
    if step.on_result is None:
        return False
    conditions = step.on_result.conditions or []
    for cond in conditions:
        if cond.when is None:
            continue  # catch-all — does not count as a gate
        if cond.when.strip() == "true":
            continue  # explicit catch-all — does not count
        # Check if any declared output is referenced in this condition
        refs = _RESULT_REF_RE.findall(cond.when)
        for ref in refs:
            if ref in declared_outputs:
                return True
    return False


def _push_reachable(
    graph: dict[str, set[str]],
    start: str,
    recipe,
    max_hops: int = _MAX_HOPS,
) -> tuple[bool, str | None]:
    """Return (reachable, push_step_name) if push_to_remote is reachable within max_hops.

    Returns (False, None) if no push_to_remote step is reachable.
    """
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(start, 0)])
    while queue:
        name, hops = queue.popleft()
        if name in visited:
            continue
        if hops > max_hops:
            continue
        visited.add(name)
        step = recipe.steps.get(name)
        if step is not None and step.tool == "push_to_remote":
            return True, name
        for succ in graph.get(name, set()):
            queue.append((succ, hops + 1))
    return False, None


@semantic_rule(
    name="conditional-skill-ungated-push",
    description=(
        "A step invoking a write_behavior='conditional' skill must not reach a "
        "push_to_remote tool step without an on_result: gate that dispatches on a "
        "declared verdict output. Unconditional on_success: to push creates a "
        "no-op loop: a zero-fix run silently re-pushes, causing infinite CI retries."
    ),
    severity=Severity.ERROR,
)
def _check_conditional_skill_ungated_push(ctx: ValidationContext) -> list[RuleFinding]:
    """Fire when a conditional-write skill step can reach push_to_remote without a verdict gate."""
    try:
        manifest = load_bundled_manifest()
    except Exception:
        logger.warning(
            "conditional-skill-ungated-push: failed to load manifest; skipping",
            exc_info=True,
        )
        return []

    skills = manifest.get("skills", {})
    findings: list[RuleFinding] = []

    for step_name, step in ctx.recipe.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        skill_cmd = (step.with_args or {}).get("skill_command", "")
        skill = resolve_skill_name(skill_cmd)
        if skill is None:
            continue
        skill_data = skills.get(skill)
        if skill_data is None:
            continue
        if skill_data.get("write_behavior") != "conditional":
            continue

        # Check that push_to_remote is reachable before firing any finding.
        # Steps that don't lead to push (e.g. worktree-fix → merge_worktree)
        # are not in scope for this rule.
        push_reachable, push_step = _push_reachable(ctx.step_graph, step_name, ctx.recipe)
        if not push_reachable:
            continue

        declared = _declared_output_names(skill_data)

        # If the step is already gated on a declared output via on_result:, it passes.
        # This allows skills like resolve-merge-conflicts that gate on their own
        # declared outputs (e.g. escalation_required) without needing a 'verdict' field.
        if _step_gated_on_declared_output(step, declared):
            continue

        # The step is NOT properly gated — fire an error.
        if not declared:
            findings.append(
                RuleFinding(
                    rule="conditional-skill-ungated-push",
                    step_name=step_name,
                    severity=Severity.ERROR,
                    message=(
                        f"Step '{step_name}' invokes skill '{skill}' "
                        f"(write_behavior=conditional) but the skill declares no "
                        f"outputs; add a typed output field (e.g. 'verdict') to the "
                        f"skill contract and gate this step with on_result: before "
                        f"reaching push step '{push_step}'."
                    ),
                )
            )
        else:
            findings.append(
                RuleFinding(
                    rule="conditional-skill-ungated-push",
                    step_name=step_name,
                    severity=Severity.ERROR,
                    message=(
                        f"Step '{step_name}' (skill '{skill}', write_behavior=conditional) "
                        f"reaches push step '{push_step}' without an on_result: gate "
                        f"on a declared verdict output. "
                        f"Replace 'on_success:' with an 'on_result:' block that "
                        f"dispatches on one of: {sorted(declared)}. "
                        f"An unconditional push path allows a zero-fix run to "
                        f"re-push silently, looping CI indefinitely."
                    ),
                )
            )

    return findings
