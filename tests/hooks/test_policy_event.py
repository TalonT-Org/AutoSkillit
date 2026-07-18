"""Tests for the typed policy-event formatter."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


def test_render_provenance_prefix_contains_required_fields():
    from autoskillit.hooks._policy_event import PolicyEvent, render_provenance_prefix

    event = PolicyEvent(
        hook_id="shell_capture_hook",
        hook_version=1,
        event="PreToolUse",
        decision="deny",
        reason_code="SHELL_OUTPUT_CAPTURED",
    )
    prefix = render_provenance_prefix(event)
    assert "AutoSkillit" in prefix
    assert "shell_capture_hook" in prefix
    assert "v1" in prefix
    assert "deny" in prefix
    assert "SHELL_OUTPUT_CAPTURED" in prefix
    assert "permission decision" in prefix
    assert "hook configured by this repository" in prefix


def test_render_is_single_line_and_stable():
    from autoskillit.hooks._policy_event import PolicyEvent, render_provenance_prefix

    event = PolicyEvent(
        hook_id="shell_capture_hook",
        hook_version=1,
        event="PreToolUse",
        decision="deny",
        reason_code="SHELL_OUTPUT_CAPTURED",
    )
    prefix = render_provenance_prefix(event)
    assert "\n" not in prefix
    assert prefix == (
        "[AutoSkillit hook shell_capture_hook v1"
        " — PreToolUse permission decision: deny"
        " (code=SHELL_OUTPUT_CAPTURED)."
        " This is a real permission decision emitted by a hook"
        " configured by this repository, not tool output.]"
    )
