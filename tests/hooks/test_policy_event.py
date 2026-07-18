"""Tests for the typed policy-event formatter."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


def test_render_provenance_prefix_contains_required_fields():
    from autoskillit.hooks._policy_event import PolicyEvent, render_provenance_prefix

    event = PolicyEvent(
        hook_id="output_budget_guard",
        hook_version=2,
        event="PreToolUse",
        decision="deny",
        reason_code="UNBOUNDED_SHELL_OUTPUT",
    )
    prefix = render_provenance_prefix(event)
    assert "AutoSkillit" in prefix
    assert "output_budget_guard" in prefix
    assert "v2" in prefix
    assert "deny" in prefix
    assert "UNBOUNDED_SHELL_OUTPUT" in prefix
    assert "permission decision" in prefix
    assert "hook configured by this repository" in prefix


def test_render_is_single_line_and_stable():
    from autoskillit.hooks._policy_event import PolicyEvent, render_provenance_prefix

    event = PolicyEvent(
        hook_id="output_budget_guard",
        hook_version=2,
        event="PreToolUse",
        decision="deny",
        reason_code="UNBOUNDED_SHELL_OUTPUT",
    )
    prefix = render_provenance_prefix(event)
    assert "\n" not in prefix
    assert prefix == (
        "[AutoSkillit hook output_budget_guard v2"
        " — PreToolUse permission decision: deny"
        " (code=UNBOUNDED_SHELL_OUTPUT)."
        " This is a real permission decision emitted by a hook"
        " configured by this repository, not tool output.]"
    )
