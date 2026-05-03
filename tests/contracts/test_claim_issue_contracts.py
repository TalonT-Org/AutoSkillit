"""Contract tests for claim_issue and release_issue MCP tools."""

from __future__ import annotations

from autoskillit.core.types import GATED_TOOLS


def test_claim_issue_in_gated_tools() -> None:
    """claim_issue must be in GATED_TOOLS — requires open kitchen."""
    assert "claim_issue" in GATED_TOOLS


def test_release_issue_in_gated_tools() -> None:
    """release_issue must be in GATED_TOOLS — requires open kitchen."""
    assert "release_issue" in GATED_TOOLS


def test_claim_issue_tool_registered() -> None:
    """claim_issue must be importable from tools_issue_lifecycle."""
    from autoskillit.server.tools.tools_issue_lifecycle import claim_issue

    assert callable(claim_issue)


def test_release_issue_tool_registered() -> None:
    """release_issue must be importable from tools_issue_lifecycle."""
    from autoskillit.server.tools.tools_issue_lifecycle import release_issue

    assert callable(release_issue)
