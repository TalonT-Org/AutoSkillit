"""Instruction-text builders used by the orchestrator's load pipeline.

Pure functions; no cache, no monkeypatch surface, no pipeline state.
"""

from __future__ import annotations

import json

from autoskillit.core import (
    ROUTING_AUTHORITY_CLAUSE,
    STEP_SKIP_SEMANTICS_CLAUSE,
    build_parameter_forwarding_rules,
    get_logger,
)
from autoskillit.recipe._rule_helpers import (
    _is_failure_sentinel_value,
    extract_sentinel_json_blocks,
)
from autoskillit.recipe.schema import Recipe

logger = get_logger(__name__)

__all__ = ["_build_orchestration_rules", "_build_stop_step_semantics", "_infer_stop_failure"]


def _infer_stop_failure(name: str, message: str | None) -> bool:
    """Determine whether a stop step represents a failure outcome.

    Parses embedded sentinel JSON first; falls back to name-based heuristic.
    """
    if message:
        for block in extract_sentinel_json_blocks(message):
            try:
                parsed = json.loads(block)
                if isinstance(parsed, dict) and "success" in parsed:
                    return _is_failure_sentinel_value(parsed["success"])
            except (json.JSONDecodeError, ValueError):
                logger.debug("sentinel_json_parse_failed", step=name, raw=block)
                continue
    return "escalate" in name.lower() or "reject" in name.lower()


def _build_stop_step_semantics(recipe: Recipe) -> str:
    stop_steps = {name: step for name, step in recipe.steps.items() if step.action == "stop"}
    if not stop_steps:
        return ""
    lines = [
        "ACTION: STOP STEP SEMANTICS:",
        "- Stop steps are terminal — the pipeline ends when routed to them.",
        "- Do NOT call any MCP tools after a stop step.",
        "- Do NOT attempt recovery, error reporting, or off-recipe actions.",
        "- When routed to a stop step, emit the L3 sentinel block and TERMINATE.",
    ]
    for name, step in stop_steps.items():
        is_failure = _infer_stop_failure(name, step.message)
        success_val = "false" if is_failure else "true"
        lines.append(
            f"- For stop step '{name}': emit the L3 sentinel block with "
            f"success={success_val} and reason=<step message>. Then TERMINATE."
        )
        if step.message:
            lines.append(f"  Stop step '{name}' message: {step.message!r}")
    return "\n".join(lines)


def _build_orchestration_rules(
    recipe: Recipe | None = None, stop_semantics: str | None = None
) -> str:
    parts = [
        STEP_SKIP_SEMANTICS_CLAUSE,
        "STEP EXECUTION IS NOT DISCRETIONARY:\n"
        "You MUST execute every step the pipeline routes you to. "
        "skip_when_false ingredient references are resolved server-side before the recipe "
        'is served. You may see literal "false" values (skip the step) '
        "or no skip_when_false field at all (step is mandatory). Resolved content "
        "contains neither skip_when_false nor its configuration-only on_skip continuation. "
        "NEVER skip a step because the PR is small, the diff is trivial, or you judge "
        "the step unnecessary. NEVER replace recipe steps with manual tool calls. "
        "Consequence: skipping PR review steps results in unreviewed code, missing "
        "diff annotations, and no architectural lens analysis.\n\n" + ROUTING_AUTHORITY_CLAUSE,
    ]
    forwarding_rules = build_parameter_forwarding_rules()
    if forwarding_rules:
        parts.append(forwarding_rules)
    if recipe is not None:
        sem = stop_semantics if stop_semantics is not None else _build_stop_step_semantics(recipe)
        if sem:
            parts.append(sem)
    parts.append(
        "ACTION: ROUTE STEP SEMANTICS:\n"
        '- When you reach a step with action: "route", evaluate the step\'s on_result\n'
        "  conditions against captured context variables. Route to the matching target.\n"
        "- Do NOT call any MCP tools for this step type — routing evaluation IS the step.\n"
        "- If no on_result condition matches and on_failure is defined, follow on_failure."
    )
    return "\n\n".join(parts)
