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

# Routing contract: RetryReason → (expected_route_keyword, evidence_condition_keyword_or_None)
_EXPECTED_ROUTES: dict[RetryReason, tuple[str, str | None]] = {
    RetryReason.RESUME: ("on_context_limit", "subtype"),
    RetryReason.STALE: ("on_failure", None),
    RetryReason.DRAIN_RACE: ("on_context_limit", None),
    RetryReason.COMPLETED_NO_FLUSH: ("on_context_limit", None),
    RetryReason.EMPTY_OUTPUT: ("on_failure", None),
    RetryReason.PATH_CONTAMINATION: ("on_failure", None),
    RetryReason.THINKING_STALL: ("on_context_limit", "lifespan_started"),
    RetryReason.IDLE_STALL: ("on_context_limit", "lifespan_started"),
    RetryReason.EARLY_STOP: ("on_context_limit", "worktree_path"),
    RetryReason.ZERO_WRITES: ("on_context_limit", "worktree_path"),
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


@pytest.mark.parametrize(
    "reason,expected",
    _EXPECTED_ROUTES.items(),
    ids=[r.value for r in _EXPECTED_ROUTES],
)
def test_reason_routes_to_expected_destination(
    reason: RetryReason,
    expected: tuple[str, str | None],
) -> None:
    """Every RetryReason must route to its declared destination in _prompts.py."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt_text = _build_orchestrator_prompt("test-recipe", mcp_prefix=DIRECT_PREFIX)
    idx = prompt_text.find(reason.value)
    assert idx != -1, f"{reason.value} not found in orchestrator prompt"

    window = prompt_text[idx : idx + 600]
    route_keyword, evidence_keyword = expected
    assert route_keyword in window, (
        f"{reason.value} must reference '{route_keyword}' within 600 chars"
    )
    if evidence_keyword:
        assert evidence_keyword in window, (
            f"{reason.value} routing must reference evidence signal '{evidence_keyword}'"
        )


def test_expected_routes_covers_all_orchestrator_visible_reasons() -> None:
    """_EXPECTED_ROUTES must have an entry for every non-excluded RetryReason."""
    missing = [
        r.name for r in RetryReason if r not in _ROUTING_EXCLUDED and r not in _EXPECTED_ROUTES
    ]
    assert not missing, f"Add routing expectation for: {missing}"
