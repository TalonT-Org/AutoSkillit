"""Routing completeness guard: every orchestrator-visible RetryReason must have a routing rule.

Prevents future RetryReason additions from silently missing routing rules in the
orchestrator prompt — the same class of oversight that produced the EMPTY_OUTPUT bug.
"""

from __future__ import annotations

import pytest

from autoskillit.cli._mcp_names import DIRECT_PREFIX
from autoskillit.core.types import RetryReason

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

# Reasons excluded from orchestrator-prompt routing check:
# - NONE: not a retry scenario, no routing needed
# - BUDGET_EXHAUSTED: caps other reasons; orchestrator never sees it directly
# - CONTRACT_RECOVERY: handled by infrastructure nudge in headless/__init__.py
# - CLONE_CONTAMINATION: handled by clone_guard infrastructure
_ROUTING_EXCLUDED = {
    RetryReason.NONE,
    RetryReason.BUDGET_EXHAUSTED,
    RetryReason.CONTRACT_RECOVERY,
    RetryReason.CLONE_CONTAMINATION,
}


def test_all_retry_reasons_have_routing_rules() -> None:
    """Every orchestrator-visible RetryReason must have an explicit routing rule."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt_text = _build_orchestrator_prompt("test-recipe", mcp_prefix=DIRECT_PREFIX)
    missing = []
    for reason in RetryReason:
        if reason in _ROUTING_EXCLUDED:
            continue
        if reason.value not in prompt_text:
            missing.append(reason.name)

    assert not missing, (
        f"RetryReason values missing routing rules in orchestrator prompt: {missing}"
    )


def test_completed_no_flush_routes_to_on_context_limit() -> None:
    """completed_no_flush routing rule must reference on_context_limit, not on_failure."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt_text = _build_orchestrator_prompt("test-recipe", mcp_prefix=DIRECT_PREFIX)
    idx = prompt_text.find("completed_no_flush")
    assert idx != -1, "completed_no_flush not found in orchestrator prompt"

    surrounding = prompt_text[idx : idx + 500]
    assert "on_context_limit" in surrounding, (
        "completed_no_flush rule must reference on_context_limit"
    )
    assert "NEVER route" in prompt_text[idx : idx + 600]


def test_empty_output_routing_does_not_include_on_context_limit() -> None:
    """empty_output routing rule must reference on_failure, not on_context_limit."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt_text = _build_orchestrator_prompt("test-recipe", mcp_prefix=DIRECT_PREFIX)
    idx = prompt_text.find("retry_reason: empty_output")
    assert idx != -1, "empty_output routing rule not found in orchestrator prompt"

    surrounding = prompt_text[idx : idx + 400]
    assert "on_failure" in surrounding, "empty_output rule must reference on_failure"
