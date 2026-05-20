"""Tests for liveness-aware claiming in claim_issue and claim_and_resolve_issue."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.fleet import DispatchRecord, DispatchStatus
from autoskillit.server.tools.tools_issue_composite import claim_and_resolve_issue
from autoskillit.server.tools.tools_issue_lifecycle import claim_issue

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

_ISSUE_URL = "https://github.com/owner/repo/issues/42"
_HELPERS_MODULE = "autoskillit.server.tools._claim_helpers"


def _make_dead_dispatch() -> DispatchRecord:
    return DispatchRecord(
        name="task-1",
        status=DispatchStatus.RUNNING,
        dispatched_pid=99999,
        dispatched_boot_id="dead-boot",
        dispatched_starttime_ticks=0,
        sidecar_path="/tmp/s.jsonl",
    )


def _mock_client_with_in_progress_label() -> AsyncMock:
    client = AsyncMock()
    client.fetch_issue.return_value = {
        "success": True,
        "labels": [{"name": "in-progress"}],
    }
    client.fetch_title.return_value = {
        "success": True,
        "title": "Test Issue",
        "slug": "test-issue",
    }
    client.swap_labels.return_value = {"success": True}
    client.ensure_label.return_value = {"success": True}
    return client


class TestClaimIssueLiveness:
    @pytest.mark.anyio
    async def test_claim_issue_proceeds_when_owning_session_is_dead(self, tool_ctx_kitchen_open):
        """When dispatch is dead, label is cleaned up and claimed=True is returned."""
        dead_dispatch = _make_dead_dispatch()
        cleanup_mock = AsyncMock()
        tool_ctx_kitchen_open.github_client = _mock_client_with_in_progress_label()

        with (
            patch(f"{_HELPERS_MODULE}.find_dispatch_for_issue", return_value=dead_dispatch),
            patch(f"{_HELPERS_MODULE}.is_dispatch_session_alive", return_value=False),
            patch(f"{_HELPERS_MODULE}.cleanup_orphaned_labels", cleanup_mock),
        ):
            result = json.loads(await claim_issue(_ISSUE_URL))

        assert result["success"] is True
        assert result["claimed"] is True
        cleanup_mock.assert_called_once_with(
            dead_dispatch.sidecar_path, tool_ctx_kitchen_open.github_client
        )

    @pytest.mark.anyio
    async def test_claim_issue_blocks_when_owning_session_is_alive(self, tool_ctx_kitchen_open):
        """When dispatch is alive, claiming is blocked (claimed=False)."""
        alive_dispatch = _make_dead_dispatch()
        tool_ctx_kitchen_open.github_client = _mock_client_with_in_progress_label()

        with (
            patch(f"{_HELPERS_MODULE}.find_dispatch_for_issue", return_value=alive_dispatch),
            patch(f"{_HELPERS_MODULE}.is_dispatch_session_alive", return_value=True),
            patch(f"{_HELPERS_MODULE}.cleanup_orphaned_labels", AsyncMock()),
        ):
            result = json.loads(await claim_issue(_ISSUE_URL))

        assert result["success"] is True
        assert result["claimed"] is False

    @pytest.mark.anyio
    async def test_claim_issue_blocks_when_no_dispatch_found(self, tool_ctx_kitchen_open):
        """When no dispatch found for issue, claiming is blocked (manual label or unknown)."""
        tool_ctx_kitchen_open.github_client = _mock_client_with_in_progress_label()

        with patch(f"{_HELPERS_MODULE}.find_dispatch_for_issue", return_value=None):
            result = json.loads(await claim_issue(_ISSUE_URL))

        assert result["success"] is True
        assert result["claimed"] is False
        assert "another session may be processing it" in result["reason"]

    @pytest.mark.anyio
    async def test_cleanup_not_called_when_dispatch_is_alive(self, tool_ctx_kitchen_open):
        """cleanup_orphaned_labels is NOT called when the owning session is alive."""
        alive_dispatch = _make_dead_dispatch()
        cleanup_mock = AsyncMock()
        tool_ctx_kitchen_open.github_client = _mock_client_with_in_progress_label()

        with (
            patch(f"{_HELPERS_MODULE}.find_dispatch_for_issue", return_value=alive_dispatch),
            patch(f"{_HELPERS_MODULE}.is_dispatch_session_alive", return_value=True),
            patch(f"{_HELPERS_MODULE}.cleanup_orphaned_labels", cleanup_mock),
        ):
            await claim_issue(_ISSUE_URL)

        cleanup_mock.assert_not_called()


class TestClaimAndResolveIssueLiveness:
    @pytest.mark.anyio
    async def test_claim_and_resolve_proceeds_when_owning_session_is_dead(
        self, tool_ctx_kitchen_open
    ):
        """claim_and_resolve_issue: dead dispatch → cleanup → claimed=True."""
        dead_dispatch = _make_dead_dispatch()
        cleanup_mock = AsyncMock()
        tool_ctx_kitchen_open.github_client = _mock_client_with_in_progress_label()

        with (
            patch(f"{_HELPERS_MODULE}.find_dispatch_for_issue", return_value=dead_dispatch),
            patch(f"{_HELPERS_MODULE}.is_dispatch_session_alive", return_value=False),
            patch(f"{_HELPERS_MODULE}.cleanup_orphaned_labels", cleanup_mock),
        ):
            result = json.loads(await claim_and_resolve_issue(_ISSUE_URL))

        assert result["success"] is True
        assert result["claimed"] is True
        cleanup_mock.assert_called_once()

    @pytest.mark.anyio
    async def test_claim_and_resolve_blocks_when_owning_session_is_alive(
        self, tool_ctx_kitchen_open
    ):
        """claim_and_resolve_issue: alive dispatch → claimed=False with issue metadata."""
        alive_dispatch = _make_dead_dispatch()
        tool_ctx_kitchen_open.github_client = _mock_client_with_in_progress_label()

        with (
            patch(f"{_HELPERS_MODULE}.find_dispatch_for_issue", return_value=alive_dispatch),
            patch(f"{_HELPERS_MODULE}.is_dispatch_session_alive", return_value=True),
            patch(f"{_HELPERS_MODULE}.cleanup_orphaned_labels", AsyncMock()),
        ):
            result = json.loads(await claim_and_resolve_issue(_ISSUE_URL))

        assert result["success"] is True
        assert result["claimed"] is False
        assert "issue_title" in result
        assert "timings" in result


class TestClaimHelperParity:
    @pytest.mark.anyio
    async def test_claim_helper_dead_dispatch_matches_tool_behavior(self):
        """_try_claim_with_liveness directly: dead dispatch → claimed=True, stale_label_cleaned."""
        from autoskillit.server.tools._claim_helpers import _try_claim_with_liveness

        dead_dispatch = _make_dead_dispatch()
        cleanup_mock = AsyncMock()

        with (
            patch(f"{_HELPERS_MODULE}.find_dispatch_for_issue", return_value=dead_dispatch),
            patch(f"{_HELPERS_MODULE}.is_dispatch_session_alive", return_value=False),
            patch(f"{_HELPERS_MODULE}.cleanup_orphaned_labels", cleanup_mock),
        ):
            decision = await _try_claim_with_liveness(
                issue_url=_ISSUE_URL,
                issue_number=42,
                effective_label="in-progress",
                current_labels=["in-progress"],
                allow_reentry=False,
                github_client=AsyncMock(),
                campaign_state_paths=[],
            )

        assert decision.claimed is True
        assert decision.stale_label_cleaned is True
        assert decision.reentry is False

    @pytest.mark.anyio
    async def test_claim_helper_alive_dispatch_matches_tool_behavior(self):
        """_try_claim_with_liveness directly: alive dispatch → claimed=False."""
        from autoskillit.server.tools._claim_helpers import _try_claim_with_liveness

        alive_dispatch = _make_dead_dispatch()

        with (
            patch(f"{_HELPERS_MODULE}.find_dispatch_for_issue", return_value=alive_dispatch),
            patch(f"{_HELPERS_MODULE}.is_dispatch_session_alive", return_value=True),
        ):
            decision = await _try_claim_with_liveness(
                issue_url=_ISSUE_URL,
                issue_number=42,
                effective_label="in-progress",
                current_labels=["in-progress"],
                allow_reentry=False,
                github_client=AsyncMock(),
                campaign_state_paths=[],
            )

        assert decision.claimed is False
        assert decision.reason != ""
