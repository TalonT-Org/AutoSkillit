"""Shared assertion helpers for stop-step and routing-clause doctrine in CLI prompts."""

from __future__ import annotations

# Doctrine phrases every stop-step rendering must surface verbatim. Shared
# verbatim by the orchestrator prompt test (tests/cli/test_orchestrator_prompt_contract.py)
# and the sous-chef SKILL content test (tests/cli/test_sous_chef_content.py).
# The food-truck prompt test (tests/fleet/test_food_truck_prompt.py) and the
# recipe-API test (tests/recipe/test_api.py) assert *overlapping-but-not-identical*
# phrase lists ("diagnostic result is supplemental" vs "diagnostic result", etc.)
# and stay on their own lists by design — they verify different doctrinally
# distinct renderings.
STOP_STEP_DOCTRINE_KEY_PHRASES: tuple[str, ...] = (
    "original failed run_skill response",
    "exact result",
    "reason_kind",
    "outcome_fields",
    "verbatim",
    "diagnostic result",
    "no structured original reason",
    "do not infer",
    "static stop message only when no tool evidence exists",
)


def slice_routing_clause(content: str, start: int) -> str:
    """Return the routing clause beginning at ``start`` up to the next top-level entry.

    The orchestrator prompt and sous-chef SKILL each render one
    ``retry_reason`` clause as a column-0 bullet followed by indented
    continuation lines. Slicing to the next column-0 entry anchors on
    structure rather than a fixed character window, so the test still
    verifies routing even if continuation lines grow with future edits.
    """
    next_entry = content.find("\n- ", start + 1)
    return content[start:] if next_entry == -1 else content[start:next_entry]
