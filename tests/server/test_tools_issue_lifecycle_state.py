"""Tests for issue lifecycle state guards, release/close, and investigation-complete paths."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.server.tools.tools_issue_composite import claim_and_resolve_issue
from autoskillit.server.tools.tools_issue_labels import claim_issue, release_issue

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.fixture
def tool_ctx_kitchen_open(tool_ctx):
    """Open the gate while retaining production backend compatibility metadata."""
    tool_ctx.gate = DefaultGateState(enabled=True)
    return tool_ctx


# ---------------------------------------------------------------------------
# State guard tests (1A)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_claim_and_resolve_rejects_closed_issue(tool_ctx_kitchen_open) -> None:
    """State guard: claim_and_resolve_issue returns claimed=False for closed issues."""
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.fetch_title = AsyncMock(
        return_value={"success": True, "number": 42, "title": "Fix bug", "slug": "fix-bug"}
    )
    tool_ctx_kitchen_open.github_client.fetch_issue = AsyncMock(
        return_value={"success": True, "state": "closed", "labels": [], "body": ""}
    )

    result = json.loads(await claim_and_resolve_issue("https://github.com/owner/repo/issues/42"))
    assert result["success"] is True
    assert result["claimed"] is False
    assert result["reason"] == "issue is closed"


@pytest.mark.anyio
async def test_claim_issue_rejects_closed_issue(tool_ctx_kitchen_open) -> None:
    """State guard: claim_issue returns claimed=False for closed issues."""
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.fetch_issue = AsyncMock(
        return_value={"success": True, "state": "closed", "labels": [], "body": ""}
    )

    result = json.loads(await claim_issue("https://github.com/owner/repo/issues/42"))
    assert result["success"] is True
    assert result["claimed"] is False
    assert result["reason"] == "issue is closed"


@pytest.mark.anyio
async def test_claim_and_resolve_accepts_open_issue(tool_ctx_kitchen_open) -> None:
    """Regression guard: claim_and_resolve_issue proceeds normally for open issues."""
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.fetch_title = AsyncMock(
        return_value={"success": True, "number": 42, "title": "Fix bug", "slug": "fix-bug"}
    )
    tool_ctx_kitchen_open.github_client.fetch_issue = AsyncMock(
        return_value={"success": True, "state": "open", "labels": [], "body": ""}
    )
    tool_ctx_kitchen_open.github_client.ensure_label = AsyncMock(return_value={"success": True})
    tool_ctx_kitchen_open.github_client.swap_labels = AsyncMock(return_value={"success": True})

    result = json.loads(await claim_and_resolve_issue("https://github.com/owner/repo/issues/42"))
    assert result["success"] is True
    assert result["claimed"] is True


# ---------------------------------------------------------------------------
# release_issue close_issue flag tests (1E)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_release_issue_with_close_flag_closes_issue(tool_ctx_kitchen_open) -> None:
    """close_issue='true' causes release_issue to call github_client.close_issue."""
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.swap_labels = AsyncMock(return_value={"success": True})
    tool_ctx_kitchen_open.github_client.close_issue = AsyncMock(return_value={"success": True})

    result = json.loads(
        await release_issue(
            "https://github.com/owner/repo/issues/42",
            close_issue="true",
        )
    )
    assert result["success"] is True
    tool_ctx_kitchen_open.github_client.close_issue.assert_called_once_with("owner", "repo", 42)


@pytest.mark.anyio
async def test_release_issue_without_close_flag_does_not_close(tool_ctx_kitchen_open) -> None:
    """Regression guard: release_issue without close_issue does not call close_issue."""
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.ensure_label = AsyncMock(return_value={"success": True})
    tool_ctx_kitchen_open.github_client.swap_labels = AsyncMock(return_value={"success": True})
    promotion_target = tool_ctx_kitchen_open.config.branching.promotion_target

    result = json.loads(
        await release_issue(
            "https://github.com/owner/repo/issues/42",
            target_branch=promotion_target,
        )
    )
    assert result["success"] is True
    tool_ctx_kitchen_open.github_client.close_issue.assert_not_called()


@pytest.mark.anyio
async def test_release_issue_close_issue_with_non_default_branch_stages_issue(
    tool_ctx_kitchen_open,
) -> None:
    """close_issue + non-default target_branch → Branch 1 (staging), not close."""
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.ensure_label = AsyncMock(return_value={"success": True})
    tool_ctx_kitchen_open.github_client.swap_labels = AsyncMock(return_value={"success": True})
    promotion_target = tool_ctx_kitchen_open.config.branching.promotion_target

    result = json.loads(
        await release_issue(
            "https://github.com/owner/repo/issues/42",
            close_issue="true",
            target_branch="develop",
        )
    )
    assert result["success"] is True
    assert result.get("staged") is True
    tool_ctx_kitchen_open.github_client.close_issue.assert_not_called()
    assert (
        promotion_target != "develop"
    )  # invariant: fixture must use a non-develop promotion target


@pytest.mark.anyio
async def test_release_issue_close_issue_with_promotion_target_closes_issue(
    tool_ctx_kitchen_open,
) -> None:
    """close_issue + promotion_target branch → Branch 3 (bare removal + close)."""
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.swap_labels = AsyncMock(return_value={"success": True})
    tool_ctx_kitchen_open.github_client.close_issue = AsyncMock(return_value={"success": True})
    promotion_target = tool_ctx_kitchen_open.config.branching.promotion_target

    result = json.loads(
        await release_issue(
            "https://github.com/owner/repo/issues/42",
            close_issue="true",
            target_branch=promotion_target,
        )
    )
    assert result["success"] is True
    assert result.get("staged") is False
    tool_ctx_kitchen_open.github_client.close_issue.assert_called_once_with("owner", "repo", 42)


@pytest.mark.anyio
async def test_release_issue_empty_string_target_branch_falls_to_bare_removal(
    tool_ctx_kitchen_open,
) -> None:
    """Empty string target_branch must fall to Branch 3 (bare removal), not spurious staging."""
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.swap_labels = AsyncMock(return_value={"success": True})

    result = json.loads(
        await release_issue(
            "https://github.com/owner/repo/issues/42",
            target_branch="",
        )
    )
    assert result["success"] is True
    assert result.get("staged") is False
    tool_ctx_kitchen_open.github_client.ensure_label.assert_not_called()


# ---------------------------------------------------------------------------
# T4: claim_issue returns investigation_complete in all 5 paths
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_claim_issue_returns_investigation_complete_closed_path(
    tool_ctx_kitchen_open,
) -> None:
    """investigation_complete: true in closed-issue path when marker present."""
    issue_data = {
        "success": True,
        "state": "closed",
        "labels": [],
        "body": (
            "## Investigation\n\n<!-- investigation_complete: true -->\n> Prior investigation."
        ),
    }
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.fetch_issue = AsyncMock(return_value=issue_data)

    result = json.loads(await claim_issue("https://github.com/owner/repo/issues/42"))
    assert result["investigation_complete"] is True
    assert "review_approach_recommended" in result


@pytest.mark.anyio
async def test_claim_issue_returns_investigation_complete_not_claimed(
    tool_ctx_kitchen_open,
) -> None:
    """investigation_complete present in not-claimed path."""
    issue_data = {
        "success": True,
        "state": "open",
        "labels": [{"name": "autoskillit:in-progress"}],
        "body": (
            "## Investigation\n\n<!-- investigation_complete: true -->\n> Prior investigation."
        ),
    }
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.fetch_issue = AsyncMock(return_value=issue_data)

    result = json.loads(
        await claim_issue(
            "https://github.com/owner/repo/issues/42",
            label="autoskillit:in-progress",
            allow_reentry=False,
        )
    )
    assert "investigation_complete" in result
    assert "review_approach_recommended" in result


@pytest.mark.anyio
async def test_claim_issue_returns_investigation_complete_reentry(
    tool_ctx_kitchen_open,
) -> None:
    """investigation_complete present in reentry path."""
    issue_data = {
        "success": True,
        "state": "open",
        "labels": [{"name": "autoskillit:in-progress"}],
        "body": (
            "## Investigation\n\n<!-- investigation_complete: true -->\n> Prior investigation."
        ),
    }
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.fetch_issue = AsyncMock(return_value=issue_data)

    result = json.loads(
        await claim_issue(
            "https://github.com/owner/repo/issues/42",
            label="autoskillit:in-progress",
            allow_reentry=True,
        )
    )
    assert "investigation_complete" in result
    assert result["investigation_complete"] is True


@pytest.mark.anyio
async def test_claim_issue_returns_investigation_complete_swap_failure(
    tool_ctx_kitchen_open,
) -> None:
    """investigation_complete present in swap-failure path."""
    issue_data = {
        "success": True,
        "state": "open",
        "labels": [],
        "body": (
            "## Investigation\n\n<!-- investigation_complete: true -->\n> Prior investigation."
        ),
    }
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.fetch_issue = AsyncMock(return_value=issue_data)
    tool_ctx_kitchen_open.github_client.ensure_label = AsyncMock(return_value={"success": True})
    tool_ctx_kitchen_open.github_client.swap_labels = AsyncMock(
        return_value={"success": False, "error": "label swap failed"}
    )

    result = json.loads(
        await claim_issue(
            "https://github.com/owner/repo/issues/42",
            label="autoskillit:in-progress",
        )
    )
    assert "investigation_complete" in result
    assert result["investigation_complete"] is True
    assert "review_approach_recommended" in result


@pytest.mark.anyio
async def test_claim_issue_returns_investigation_complete_success(
    tool_ctx_kitchen_open,
) -> None:
    """investigation_complete present in success path."""
    issue_data = {
        "success": True,
        "state": "open",
        "labels": [],
        "body": (
            "## Investigation\n\n<!-- investigation_complete: true -->\n> Prior investigation."
        ),
    }
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.fetch_issue = AsyncMock(return_value=issue_data)
    tool_ctx_kitchen_open.github_client.ensure_label = AsyncMock(return_value={"success": True})
    tool_ctx_kitchen_open.github_client.swap_labels = AsyncMock(return_value={"success": True})

    result = json.loads(
        await claim_issue(
            "https://github.com/owner/repo/issues/42",
            label="autoskillit:in-progress",
        )
    )
    assert "investigation_complete" in result
    assert result["investigation_complete"] is True


@pytest.mark.anyio
async def test_claim_issue_investigation_complete_false_when_marker_in_code_fence(
    tool_ctx_kitchen_open,
) -> None:
    """A marker quoted in a fenced code block is not an investigation signal."""
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.fetch_issue = AsyncMock(
        return_value={
            "success": True,
            "state": "closed",
            "labels": [],
            "body": "```\n<!-- investigation_complete: true -->\n```\nSome prose.",
        }
    )

    result = json.loads(await claim_issue("https://github.com/owner/repo/issues/42"))
    assert result["investigation_complete"] is False


@pytest.mark.anyio
async def test_claim_issue_investigation_complete_false_when_marker_in_inline_span(
    tool_ctx_kitchen_open,
) -> None:
    """A marker quoted in an inline code span is not an investigation signal."""
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.fetch_issue = AsyncMock(
        return_value={
            "success": True,
            "state": "closed",
            "labels": [],
            "body": "Check `<!-- investigation_complete: true -->` for details.",
        }
    )

    result = json.loads(await claim_issue("https://github.com/owner/repo/issues/42"))
    assert result["investigation_complete"] is False


@pytest.mark.anyio
async def test_claim_issue_investigation_complete_true_with_genuine_and_quoted_marker(
    tool_ctx_kitchen_open,
) -> None:
    """A genuine marker remains effective when another copy is fenced."""
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.fetch_issue = AsyncMock(
        return_value={
            "success": True,
            "state": "closed",
            "labels": [],
            "body": (
                "## Investigation\n\n"
                "<!-- investigation_complete: true -->\n\n"
                "```\n<!-- investigation_complete: true -->\n```"
            ),
        }
    )

    result = json.loads(await claim_issue("https://github.com/owner/repo/issues/42"))
    assert result["investigation_complete"] is True


@pytest.mark.anyio
async def test_claim_issue_review_approach_false_when_marker_in_code_fence(
    tool_ctx_kitchen_open,
) -> None:
    """A review marker quoted in a fenced code block is ignored."""
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.fetch_issue = AsyncMock(
        return_value={
            "success": True,
            "state": "closed",
            "labels": [],
            "body": "```\n<!-- review_approach: true -->\n```\nSome prose.",
        }
    )

    result = json.loads(await claim_issue("https://github.com/owner/repo/issues/42"))
    assert result["review_approach_recommended"] is False


@pytest.mark.anyio
async def test_claim_issue_review_approach_false_when_marker_in_inline_span(
    tool_ctx_kitchen_open,
) -> None:
    """A review marker quoted in an inline code span is ignored."""
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.fetch_issue = AsyncMock(
        return_value={
            "success": True,
            "state": "closed",
            "labels": [],
            "body": "Check `<!-- review_approach: true -->` for details.",
        }
    )

    result = json.loads(await claim_issue("https://github.com/owner/repo/issues/42"))
    assert result["review_approach_recommended"] is False
