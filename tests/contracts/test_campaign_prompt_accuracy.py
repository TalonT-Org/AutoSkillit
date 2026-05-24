"""Contract: campaign prompt does not contain inaccurate semaphore language."""

from __future__ import annotations

import pytest

from autoskillit.cli._prompts_campaign import _build_dynamic_dispatch_section

MCP_PREFIX = "autoskillit__"


@pytest.fixture(scope="module")
def dynamic_dispatch_text() -> str:
    return _build_dynamic_dispatch_section(mcp_prefix=MCP_PREFIX)


def test_campaign_prompt_does_not_claim_queuing(dynamic_dispatch_text: str) -> None:
    """Campaign prompt must NOT claim dispatches queue when semaphore is saturated."""
    assert "calls queue" not in dynamic_dispatch_text, (
        "Campaign prompt must not claim 'calls queue when the semaphore is saturated' — "
        "FLEET_PARALLEL_REFUSED is a fast-fail, not a queue. Dispatcher must wait and retry."
    )


def test_dynamic_dispatch_section_uses_configured_max_issues() -> None:
    """Dynamic dispatch section must use the passed max_issues_per_food_truck value."""
    text = _build_dynamic_dispatch_section(mcp_prefix=MCP_PREFIX, max_issues_per_food_truck=7)
    assert "(default: 7)" in text
    assert "(default: 5)" not in text


def test_dynamic_dispatch_section_default_max_issues_is_3() -> None:
    """Default max_issues_per_food_truck when unspecified should be 3."""
    text = _build_dynamic_dispatch_section(mcp_prefix=MCP_PREFIX)
    assert "(default: 3)" in text
