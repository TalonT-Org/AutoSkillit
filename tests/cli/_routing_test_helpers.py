"""Shared assertion helpers for stop-step and routing-clause doctrine in CLI prompts."""

from __future__ import annotations

# Doctrine phrases that every stop-step rendering must surface verbatim. The
# orchestrator prompt, sous-chef SKILL, food-truck prompt, and recipe-API test
# all assert the same set, so the list lives here to keep them in sync.
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

    Step-rendering helpers (orchestrator prompt, sous-chef SKILL, food-truck
    prompt) emit each ``retry_reason`` clause as a column-0 bullet followed by
    2-space-indented continuation lines. Slicing to the next column-0 entry
    anchors on structure rather than a fixed character window, so the test
    still verifies routing even if continuation lines grow with future edits.
    """
    next_entry = content.find("\n- ", start + 1)
    return content[start:] if next_entry == -1 else content[start:next_entry]
