"""Shared fixtures for tests/server/."""

from __future__ import annotations

import pytest

from autoskillit.core import SkillResult
from autoskillit.core.types import RetryReason


@pytest.fixture()
def kitchen_enabled():
    """Enable the kitchen tag on the MCP server for the duration of the test."""
    from autoskillit.server import mcp

    mcp.enable(tags={"kitchen"})
    yield
    mcp.disable(tags={"kitchen"})


@pytest.fixture()
def headless_enabled():
    """Enable the headless tag on the MCP server for the duration of the test."""
    from autoskillit.server import mcp

    mcp.enable(tags={"headless"})
    yield
    mcp.disable(tags={"headless"})


# ---------------------------------------------------------------------------
# Shared SkillResult builders (used by report_bug and prepare/enrich_issues tests)
# ---------------------------------------------------------------------------


def _skill_ok(report_text: str = "## Bug Report\ndetails") -> SkillResult:
    return SkillResult(
        success=True,
        result=report_text,
        session_id="sid",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
    )


def _skill_fail() -> SkillResult:
    return SkillResult(
        success=False,
        result="",
        session_id="",
        subtype="error",
        is_error=True,
        exit_code=1,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="something went wrong",
    )
