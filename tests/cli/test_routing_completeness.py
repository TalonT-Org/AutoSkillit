"""Routing completeness guard: every orchestrator-visible RetryReason must have a routing rule.

Prevents future RetryReason additions from silently missing routing rules in the
orchestrator prompt — the same class of oversight that produced the EMPTY_OUTPUT bug.
"""

from __future__ import annotations

import inspect

import pytest

from autoskillit.cli._mcp_names import DIRECT_PREFIX
from autoskillit.core import INFRASTRUCTURE_FAULT_OVERRIDE_CLAUSE
from autoskillit.core.types import RetryReason

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

# Reasons excluded from orchestrator-prompt routing check:
# - NONE: not a retry scenario, no routing needed
# - BUDGET_EXHAUSTED: caps other reasons; orchestrator never sees it directly
# - CANCELLED: transport-level event; tool handler converts to structured result at the boundary,
#   orchestrator sees success=False + subtype="cancelled" and routes via on_failure
_ROUTING_EXCLUDED = {
    RetryReason.NONE,
    RetryReason.BUDGET_EXHAUSTED,
    RetryReason.CANCELLED,
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
    RetryReason.EARLY_STOP: ("on_context_limit", "has_progress_evidence"),
    RetryReason.ZERO_WRITES: ("on_context_limit", "has_progress_evidence"),
    RetryReason.CONTRACT_RECOVERY: ("on_context_limit", "has_progress_evidence"),
    RetryReason.CLONE_CONTAMINATION: ("on_failure", "pre_contamination_retry_reason"),
    RetryReason.RATE_LIMITED: ("on_rate_limit", None),
    RetryReason.OUTCOME_INVARIANT: ("on_failure", None),
    RetryReason.ASYNC_OBLIGATION: ("on_failure", None),
}


def test_all_retry_reasons_have_routing_rules() -> None:
    """Every orchestrator-visible RetryReason must have an explicit routing rule."""
    from tests.cli._orchestrator_prompt_helpers import (
        build_orchestrator_prompt as _build_orchestrator_prompt,
    )

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
    from tests.cli._orchestrator_prompt_helpers import (
        build_orchestrator_prompt as _build_orchestrator_prompt,
    )

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


def test_infrastructure_fault_override_is_documented() -> None:
    """Orchestrator prompt must document the infra_fault_domain override.

    Asserts the literal wire key `infra_fault_domain` (not just the value
    "infrastructure") appears — a prompt that named a differently-shaped key
    `to_json()` never emits would still pass a value-only check, which is
    exactly the failure mode this test exists to catch.
    """
    from tests.cli._orchestrator_prompt_helpers import (
        build_orchestrator_prompt as _build_orchestrator_prompt,
    )

    prompt_text = _build_orchestrator_prompt("test-recipe", mcp_prefix=DIRECT_PREFIX)

    assert INFRASTRUCTURE_FAULT_OVERRIDE_CLAUSE in prompt_text
    assert "infra_fault_domain" in prompt_text, (
        "orchestrator prompt missing the infra_fault_domain wire key"
    )
    assert "infrastructure" in prompt_text, (
        "orchestrator prompt missing the infrastructure fault value"
    )
    assert "MUST NOT be followed" in prompt_text, (
        "orchestrator prompt must state on_failure MUST NOT be followed on infra fault"
    )


def test_infrastructure_fault_override_key_in_load_recipe_docstring() -> None:
    """load_recipe docstring must name the infra_fault_domain wire key."""
    from autoskillit.server.tools.tools_recipe import load_recipe

    assert load_recipe.__doc__ is not None
    normalized_doc = "\n".join(
        line.strip() for line in inspect.cleandoc(load_recipe.__doc__).splitlines()
    )
    assert INFRASTRUCTURE_FAULT_OVERRIDE_CLAUSE.strip() in normalized_doc
    assert "infra_fault_domain" in load_recipe.__doc__, (
        "load_recipe docstring missing the infra_fault_domain wire key"
    )
