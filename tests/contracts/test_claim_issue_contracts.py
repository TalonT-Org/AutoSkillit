"""Contract tests for claim_issue and release_issue MCP tools."""

from __future__ import annotations

import pytest

from autoskillit.core.types import GATED_TOOLS

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def test_claim_issue_in_gated_tools() -> None:
    """claim_issue must be in GATED_TOOLS — requires open kitchen."""
    assert "claim_issue" in GATED_TOOLS


def test_release_issue_in_gated_tools() -> None:
    """release_issue must be in GATED_TOOLS — requires open kitchen."""
    assert "release_issue" in GATED_TOOLS


def test_claim_issue_tool_registered() -> None:
    """claim_issue must be importable from tools_issue_labels."""
    from autoskillit.server.tools.tools_issue_labels import claim_issue

    assert callable(claim_issue)


def test_release_issue_tool_registered() -> None:
    """release_issue must be importable from tools_issue_labels."""
    from autoskillit.server.tools.tools_issue_labels import release_issue

    assert callable(release_issue)
