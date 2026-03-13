"""Tests for DefaultMergeQueueWatcher polling state machine."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from autoskillit.execution.merge_queue import DefaultMergeQueueWatcher


class TestDefaultMergeQueueWatcher:
    """Tests for DefaultMergeQueueWatcher polling state machine."""

    def _make_watcher(self) -> DefaultMergeQueueWatcher:
        return DefaultMergeQueueWatcher(token=None)

    def _pr_state(
        self,
        *,
        state: str = "open",
        merged: bool = False,
        auto_merge: object = None,
        mergeable_state: str = "clean",
    ) -> dict:
        return {
            "state": state,
            "merged": merged,
            "auto_merge": auto_merge,
            "mergeable_state": mergeable_state,
        }

    @pytest.mark.anyio
    async def test_returns_merged_on_first_pr_state_check(self):
        watcher = self._make_watcher()
        watcher._fetch_pr_state = AsyncMock(  # type: ignore[method-assign]
            return_value=self._pr_state(merged=True)
        )
        result = await watcher.wait(
            pr_number=42, target_branch="main", repo="owner/repo", poll_interval=1
        )
        assert result["success"] is True
        assert result["pr_state"] == "merged"

    @pytest.mark.anyio
    async def test_returns_merged_when_pr_closed_and_merged(self):
        watcher = self._make_watcher()
        watcher._fetch_pr_state = AsyncMock(  # type: ignore[method-assign]
            return_value=self._pr_state(state="closed", merged=True)
        )
        result = await watcher.wait(
            pr_number=42, target_branch="main", repo="owner/repo", poll_interval=1
        )
        assert result["success"] is True
        assert result["pr_state"] == "merged"

    @pytest.mark.anyio
    async def test_returns_ejected_when_not_in_queue_not_stuck(self):
        watcher = self._make_watcher()
        watcher._fetch_pr_state = AsyncMock(  # type: ignore[method-assign]
            return_value=self._pr_state(auto_merge=None)
        )
        watcher._fetch_queue_entries = AsyncMock(return_value=[])  # type: ignore[method-assign]
        result = await watcher.wait(
            pr_number=42, target_branch="main", repo="owner/repo", poll_interval=1
        )
        assert result["success"] is False
        assert result["pr_state"] == "ejected"

    @pytest.mark.anyio
    async def test_keeps_polling_while_pr_in_queue(self):
        watcher = self._make_watcher()
        pr_call_count = 0

        async def _pr_state_side(*_a: object, **_kw: object) -> dict:
            nonlocal pr_call_count
            pr_call_count += 1
            if pr_call_count >= 3:
                return self._pr_state(merged=True)
            return self._pr_state(merged=False)

        watcher._fetch_pr_state = _pr_state_side  # type: ignore[method-assign]
        watcher._fetch_queue_entries = AsyncMock(  # type: ignore[method-assign]
            return_value=[{"pr_number": 42, "state": "AWAITING_CHECKS"}]
        )

        with patch(
            "autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            result = await watcher.wait(
                pr_number=42, target_branch="main", repo="owner/repo", poll_interval=1
            )

        assert result["success"] is True
        assert result["pr_state"] == "merged"
        assert mock_sleep.call_count >= 2

    @pytest.mark.anyio
    async def test_stuck_detection_triggers_toggle_once(self):
        watcher = self._make_watcher()
        toggle_calls: list[int] = []
        pr_call_count = 0

        async def _pr_state_side(*_a: object, **_kw: object) -> dict:
            nonlocal pr_call_count
            pr_call_count += 1
            if pr_call_count >= 2:
                return self._pr_state(merged=True)
            return self._pr_state(
                auto_merge={"method": "squash"},
                mergeable_state="clean",
            )

        async def _toggle_side(*_a: object, **_kw: object) -> None:
            toggle_calls.append(1)

        watcher._fetch_pr_state = _pr_state_side  # type: ignore[method-assign]
        watcher._fetch_queue_entries = AsyncMock(return_value=[])  # type: ignore[method-assign]
        watcher._toggle_auto_merge = _toggle_side  # type: ignore[method-assign]

        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42, target_branch="main", repo="owner/repo", poll_interval=1
            )

        assert result["success"] is True
        assert result["pr_state"] == "merged"
        assert len(toggle_calls) == 1

    @pytest.mark.anyio
    async def test_returns_timeout_when_deadline_exceeded(self):
        watcher = self._make_watcher()
        watcher._fetch_pr_state = AsyncMock(  # type: ignore[method-assign]
            return_value=self._pr_state(merged=False)
        )
        watcher._fetch_queue_entries = AsyncMock(  # type: ignore[method-assign]
            return_value=[{"pr_number": 42, "state": "AWAITING_CHECKS"}]
        )

        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            with patch("autoskillit.execution.merge_queue.time.monotonic") as mock_time:
                # deadline=0.0+1000=1000.0; loop enters (1.0<1000); after sleep exceeds (1001.0)
                mock_time.side_effect = [0.0, 1.0, 1001.0]
                result = await watcher.wait(
                    pr_number=42,
                    target_branch="main",
                    repo="owner/repo",
                    timeout_seconds=1000,
                    poll_interval=1,
                )

        assert result["success"] is False
        assert result["pr_state"] == "timeout"
        assert "Timed out" in result["reason"]

    @pytest.mark.anyio
    async def test_returns_ejected_when_pr_closed_not_merged(self):
        watcher = self._make_watcher()
        watcher._fetch_pr_state = AsyncMock(  # type: ignore[method-assign]
            return_value=self._pr_state(state="closed", merged=False)
        )
        watcher._fetch_queue_entries = AsyncMock(return_value=[])  # type: ignore[method-assign]
        result = await watcher.wait(
            pr_number=42, target_branch="main", repo="owner/repo", poll_interval=1
        )
        assert result["success"] is False
        assert result["pr_state"] == "ejected"

    @pytest.mark.anyio
    async def test_http_error_in_pr_state_logs_and_retries(self):
        watcher = self._make_watcher()
        call_count = 0

        async def _pr_state_side(*_a: object, **_kw: object) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.HTTPStatusError(
                    "500",
                    request=httpx.Request("GET", "http://x"),
                    response=httpx.Response(500),
                )
            return self._pr_state(merged=True)

        watcher._fetch_pr_state = _pr_state_side  # type: ignore[method-assign]

        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42, target_branch="main", repo="owner/repo", poll_interval=1
            )

        assert result["success"] is True
        assert result["pr_state"] == "merged"

    @pytest.mark.anyio
    async def test_graphql_error_falls_through_as_empty_queue(self):
        watcher = self._make_watcher()
        watcher._fetch_pr_state = AsyncMock(  # type: ignore[method-assign]
            return_value=self._pr_state(auto_merge=None)
        )
        watcher._fetch_queue_entries = AsyncMock(  # type: ignore[method-assign]
            side_effect=httpx.RequestError("connection error")
        )
        result = await watcher.wait(
            pr_number=42, target_branch="main", repo="owner/repo", poll_interval=1
        )
        assert result["success"] is False
        assert result["pr_state"] == "ejected"
