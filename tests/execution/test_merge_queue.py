"""Tests for DefaultMergeQueueWatcher polling state machine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from autoskillit.execution.merge_queue import DefaultMergeQueueWatcher, PRFetchState


def _make_watcher() -> DefaultMergeQueueWatcher:
    return DefaultMergeQueueWatcher(token=None)


def _queue_state(
    *,
    merged: bool = False,
    state: str = "OPEN",
    merge_state_status: str = "CLEAN",
    auto_merge_enabled_at: datetime | None = None,
    pr_node_id: str = "PR_kwDO_test",
    in_queue: bool = False,
    queue_state: str | None = None,
    checks_state: str | None = None,
) -> PRFetchState:
    return {
        "merged": merged,
        "state": state,
        "merge_state_status": merge_state_status,
        "auto_merge_enabled_at": auto_merge_enabled_at,
        "pr_node_id": pr_node_id,
        "in_queue": in_queue,
        "queue_state": queue_state,
        "checks_state": checks_state,
    }


class TestDefaultMergeQueueWatcher:
    """Tests for DefaultMergeQueueWatcher polling state machine."""

    @pytest.mark.anyio
    async def test_returns_merged_on_first_pr_state_check(self):
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(merged=True)
        )
        result = await watcher.wait(
            pr_number=42, target_branch="main", repo="owner/repo", poll_interval=1
        )
        assert result["success"] is True
        assert result["pr_state"] == "merged"

    @pytest.mark.anyio
    async def test_returns_merged_when_pr_closed_and_merged(self):
        """merged=True takes priority over state=CLOSED."""
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(state="CLOSED", merged=True)
        )
        result = await watcher.wait(
            pr_number=42, target_branch="main", repo="owner/repo", poll_interval=1
        )
        assert result["success"] is True
        assert result["pr_state"] == "merged"

    @pytest.mark.anyio
    async def test_returns_ejected_when_not_in_queue_not_stuck(self):
        """Two consecutive 'open, not in queue, no auto_merge' cycles → ejected."""
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(
                merged=False,
                in_queue=False,
                auto_merge_enabled_at=None,
                merge_state_status="BLOCKED",
            )
        )
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42, target_branch="main", repo="owner/repo", poll_interval=1
            )
        assert result["success"] is False
        assert result["pr_state"] == "ejected"

    @pytest.mark.anyio
    async def test_keeps_polling_while_pr_in_queue(self):
        watcher = _make_watcher()
        call_count = 0

        async def _fetch_side(*_a: object, **_kw: object) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                return _queue_state(merged=True)
            return _queue_state(in_queue=True, queue_state="AWAITING_CHECKS")

        watcher._fetch_pr_and_queue_state = _fetch_side  # type: ignore[method-assign]

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
        """Stalled PR (auto_merge set, grace expired) triggers toggle and then merges."""
        watcher = _make_watcher()
        toggle_calls: list[int] = []
        call_count = 0
        enabled_at = datetime.now(UTC) - timedelta(seconds=120)

        async def _fetch_side(*_a: object, **_kw: object) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count >= 4:
                return _queue_state(merged=True)
            return _queue_state(
                auto_merge_enabled_at=enabled_at,
                merge_state_status="CLEAN",
                in_queue=False,
            )

        async def _toggle_side(*_a: object, **_kw: object) -> None:
            toggle_calls.append(1)

        watcher._fetch_pr_and_queue_state = _fetch_side  # type: ignore[method-assign]
        watcher._toggle_auto_merge = _toggle_side  # type: ignore[method-assign]

        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                stall_grace_period=60,
            )

        assert result["success"] is True
        assert result["pr_state"] == "merged"
        assert len(toggle_calls) == 1

    @pytest.mark.anyio
    async def test_returns_timeout_when_deadline_exceeded(self):
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(in_queue=True, queue_state="AWAITING_CHECKS")
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
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(state="CLOSED", merged=False)
        )
        result = await watcher.wait(
            pr_number=42, target_branch="main", repo="owner/repo", poll_interval=1
        )
        assert result["success"] is False
        assert result["pr_state"] == "ejected"

    @pytest.mark.anyio
    async def test_http_error_in_pr_state_logs_and_retries(self):
        """Fetch error is caught and retried; subsequent success returns merged."""
        watcher = _make_watcher()
        call_count = 0

        async def _fetch_side(*_a: object, **_kw: object) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.HTTPStatusError(
                    "500",
                    request=httpx.Request("GET", "http://x"),
                    response=httpx.Response(500),
                )
            return _queue_state(merged=True)

        watcher._fetch_pr_and_queue_state = _fetch_side  # type: ignore[method-assign]

        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42, target_branch="main", repo="owner/repo", poll_interval=1
            )

        assert result["success"] is True
        assert result["pr_state"] == "merged"

    @pytest.mark.anyio
    async def test_graphql_error_falls_through_as_retry(self):
        """GraphQL fetch error is caught and retried; subsequent success returns merged."""
        watcher = _make_watcher()
        call_count = 0

        async def _fetch_side(*_a: object, **_kw: object) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("GraphQL error: connection error")
            return _queue_state(merged=True)

        watcher._fetch_pr_and_queue_state = _fetch_side  # type: ignore[method-assign]

        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42, target_branch="main", repo="owner/repo", poll_interval=1
            )

        assert result["success"] is True
        assert result["pr_state"] == "merged"

    @pytest.mark.anyio
    async def test_graphql_errors_raise(self):
        """GraphQL error responses from _fetch_pr_and_queue_state should raise."""
        watcher = _make_watcher()

        async def _mock_post(*args, **kwargs):
            resp = httpx.Response(
                200,
                json={"errors": [{"message": "some error"}]},
                request=httpx.Request("POST", "http://x"),
            )
            return resp

        watcher._client.post = _mock_post  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="GraphQL error"):
            await watcher._fetch_pr_and_queue_state(42, "owner", "repo", "main")


class TestMergeQueueReliability:
    """Tests for confirmation-window ejection guard and stall detection/retry logic."""

    # Group 1: Confirmation-window ejection guard

    @pytest.mark.anyio
    async def test_no_false_ejection_when_pr_merges_between_cycles(self):
        """Cycle 1 sees 'not in queue'; cycle 2 sees merged=True → return merged."""
        watcher = _make_watcher()
        responses = [
            _queue_state(merged=False, in_queue=False),
            _queue_state(merged=True),
        ]
        watcher._fetch_pr_and_queue_state = AsyncMock(side_effect=responses)  # type: ignore[method-assign]
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42, target_branch="main", repo="owner/repo", poll_interval=1
            )
        assert result["success"] is True
        assert result["pr_state"] == "merged"

    @pytest.mark.anyio
    async def test_confirmation_window_delays_ejection_by_one_cycle(self):
        """Two consecutive 'not in queue' cycles with no auto_merge → ejected, not on cycle 1."""
        watcher = _make_watcher()
        state = _queue_state(
            merged=False, in_queue=False, auto_merge_enabled_at=None, merge_state_status="BLOCKED"
        )
        watcher._fetch_pr_and_queue_state = AsyncMock(return_value=state)  # type: ignore[method-assign]
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42, target_branch="main", repo="owner/repo", poll_interval=1
            )
        assert result["success"] is False
        assert result["pr_state"] == "ejected"
        # Confirmation window requires exactly 2 cycles before ejection
        assert watcher._fetch_pr_and_queue_state.call_count == 2  # type: ignore[union-attr]

    @pytest.mark.anyio
    async def test_single_graphql_call_per_poll_cycle(self):
        """Each poll cycle makes exactly one call to _fetch_pr_and_queue_state."""
        watcher = _make_watcher()
        call_count = 0

        async def _fetch_side(*_a: object, **_kw: object) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                return _queue_state(merged=True)
            return _queue_state(in_queue=True, queue_state="AWAITING_CHECKS")

        watcher._fetch_pr_and_queue_state = _fetch_side  # type: ignore[method-assign]
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42, target_branch="main", repo="owner/repo", poll_interval=1
            )
        assert result["success"] is True
        assert call_count == 3  # exactly 3 fetch calls (not 6 as with the old 2-call approach)

    # Group 2: Stall detection and multi-retry

    @pytest.mark.anyio
    async def test_stall_grace_period_prevents_premature_toggle(self):
        """PR stalled 30s ago with grace=60s → toggle must NOT be called within grace."""
        watcher = _make_watcher()
        toggle_calls: list[object] = []
        call_count = 0
        enabled_at = datetime.now(UTC) - timedelta(seconds=30)

        async def _fetch_side(*_a: object, **_kw: object) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count >= 4:
                return _queue_state(merged=True)
            return _queue_state(
                auto_merge_enabled_at=enabled_at,
                merge_state_status="CLEAN",
                in_queue=False,
            )

        async def _toggle_side(*_a: object, **_kw: object) -> None:
            toggle_calls.append(1)

        watcher._fetch_pr_and_queue_state = _fetch_side  # type: ignore[method-assign]
        watcher._toggle_auto_merge = _toggle_side  # type: ignore[method-assign]
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                stall_grace_period=60,
            )
        assert len(toggle_calls) == 0, "Toggle must NOT be called within grace period"
        assert result["success"] is True

    @pytest.mark.anyio
    async def test_stall_toggle_triggered_after_grace_period_expires(self):
        """PR stalled 90s ago with grace=60s → toggle IS called after grace expires."""
        watcher = _make_watcher()
        toggle_calls: list[object] = []
        call_count = 0
        enabled_at = datetime.now(UTC) - timedelta(seconds=90)

        async def _fetch_side(*_a: object, **_kw: object) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count >= 4:
                return _queue_state(merged=True)
            return _queue_state(
                auto_merge_enabled_at=enabled_at,
                merge_state_status="CLEAN",
                in_queue=False,
            )

        async def _toggle_side(*_a: object, **_kw: object) -> None:
            toggle_calls.append(1)

        watcher._fetch_pr_and_queue_state = _fetch_side  # type: ignore[method-assign]
        watcher._toggle_auto_merge = _toggle_side  # type: ignore[method-assign]
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            await watcher.wait(
                pr_number=42,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                stall_grace_period=60,
            )
        assert len(toggle_calls) >= 1, "Toggle must be called after grace period expires"

    @pytest.mark.anyio
    async def test_max_stall_retries_exhausted_returns_stalled_not_ejected(self):
        """After max_stall_retries=2 toggle attempts, returns pr_state='stalled' not 'ejected'."""
        watcher = _make_watcher()
        enabled_at = datetime.now(UTC) - timedelta(seconds=120)
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(
                merged=False,
                in_queue=False,
                auto_merge_enabled_at=enabled_at,
                merge_state_status="CLEAN",
            )
        )
        watcher._toggle_auto_merge = AsyncMock()  # type: ignore[method-assign]
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                stall_grace_period=60,
                max_stall_retries=2,
            )
        assert result["success"] is False
        assert result["pr_state"] == "stalled"
        assert result["stall_retries_attempted"] == 2

    @pytest.mark.anyio
    async def test_exponential_backoff_between_stall_retries(self):
        """Backoff durations must be [30, 60, 120] seconds for retries 0, 1, 2."""
        watcher = _make_watcher()
        enabled_at = datetime.now(UTC) - timedelta(seconds=120)
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(
                merged=False,
                in_queue=False,
                auto_merge_enabled_at=enabled_at,
                merge_state_status="CLEAN",
            )
        )
        watcher._toggle_auto_merge = AsyncMock()  # type: ignore[method-assign]
        sleep_calls: list[float] = []

        async def _capture_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        with patch("autoskillit.execution.merge_queue.asyncio.sleep", side_effect=_capture_sleep):
            result = await watcher.wait(
                pr_number=42,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                stall_grace_period=60,
                max_stall_retries=3,
            )
        assert result["pr_state"] == "stalled"
        backoff_sleeps = [s for s in sleep_calls if s > 1]
        assert backoff_sleeps == [30, 60, 120]

    @pytest.mark.anyio
    async def test_graphql_mutation_used_for_toggle(self):
        """Toggle must use disablePullRequestAutoMerge and enablePullRequestAutoMerge mutations."""
        watcher = _make_watcher()
        enabled_at = datetime.now(UTC) - timedelta(seconds=120)
        call_count = 0

        async def _fetch_side(*_a: object, **_kw: object) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count >= 4:
                return _queue_state(merged=True)
            return _queue_state(
                merged=False,
                in_queue=False,
                auto_merge_enabled_at=enabled_at,
                merge_state_status="CLEAN",
            )

        watcher._fetch_pr_and_queue_state = _fetch_side  # type: ignore[method-assign]
        captured_bodies: list[dict] = []

        async def _mock_post(url: str, **kwargs: object) -> httpx.Response:
            body = dict(kwargs.get("json", {}) or {})  # type: ignore[arg-type]
            captured_bodies.append(body)
            query = body.get("query", "")
            if "disablePullRequestAutoMerge" in query:
                data: dict = {
                    "data": {"disablePullRequestAutoMerge": {"pullRequest": {"number": 42}}}
                }
            elif "enablePullRequestAutoMerge" in query:
                data = {"data": {"enablePullRequestAutoMerge": {"pullRequest": {"number": 42}}}}
            else:
                data = {"data": {}}
            return httpx.Response(200, json=data, request=httpx.Request("POST", url))

        watcher._client.post = _mock_post  # type: ignore[method-assign]
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            await watcher.wait(
                pr_number=42,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                stall_grace_period=60,
                max_stall_retries=1,
            )
        queries = [b.get("query", "") for b in captured_bodies]
        assert any("disablePullRequestAutoMerge" in q for q in queries), (
            "Expected disable mutation"
        )
        assert any("enablePullRequestAutoMerge" in q for q in queries), "Expected enable mutation"

    @pytest.mark.anyio
    async def test_stall_recovery_success_after_toggle(self):
        """After 1 toggle PR re-enters queue then merges → success, stall_retries_attempted=1."""
        watcher = _make_watcher()
        toggle_calls: list[object] = []
        call_count = 0
        enabled_at = datetime.now(UTC) - timedelta(seconds=120)

        async def _fetch_side(*_a: object, **_kw: object) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return _queue_state(
                    merged=False,
                    in_queue=False,
                    auto_merge_enabled_at=enabled_at,
                    merge_state_status="CLEAN",
                )
            elif call_count == 3:
                return _queue_state(in_queue=True, queue_state="AWAITING_CHECKS")
            else:
                return _queue_state(merged=True)

        async def _toggle_side(*_a: object, **_kw: object) -> None:
            toggle_calls.append(1)

        watcher._fetch_pr_and_queue_state = _fetch_side  # type: ignore[method-assign]
        watcher._toggle_auto_merge = _toggle_side  # type: ignore[method-assign]
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                stall_grace_period=60,
                max_stall_retries=3,
            )
        assert result["success"] is True
        assert result["pr_state"] == "merged"
        assert result["stall_retries_attempted"] == 1

    # Group 3: Tool Interface

    @pytest.mark.anyio
    async def test_stall_retries_attempted_present_in_all_terminal_responses(self):
        """stall_retries_attempted key must be present in all four terminal responses."""
        watcher = _make_watcher()

        # merged
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(merged=True)
        )
        result = await watcher.wait(pr_number=1, target_branch="main", repo="owner/repo")
        assert "stall_retries_attempted" in result, (
            "merged response missing stall_retries_attempted"
        )
        assert result["stall_retries_attempted"] == 0, "merged: no retries expected"

        # ejected (closed)
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(state="CLOSED", merged=False)
        )
        result = await watcher.wait(pr_number=1, target_branch="main", repo="owner/repo")
        assert "stall_retries_attempted" in result, (
            "ejected response missing stall_retries_attempted"
        )
        assert result["stall_retries_attempted"] == 0, "ejected: no retries expected"

        # timeout
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            with patch("autoskillit.execution.merge_queue.time.monotonic") as mock_time:
                mock_time.side_effect = [0.0, 1.0, 1001.0]
                watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
                    return_value=_queue_state(in_queue=True, queue_state="AWAITING_CHECKS")
                )
                result = await watcher.wait(
                    pr_number=1,
                    target_branch="main",
                    repo="owner/repo",
                    timeout_seconds=1000,
                )
        assert "stall_retries_attempted" in result, (
            "timeout response missing stall_retries_attempted"
        )
        assert result["stall_retries_attempted"] == 0, "timeout: no retries expected"
        assert result["pr_state"] == "timeout"

        # stalled
        enabled_at = datetime.now(UTC) - timedelta(seconds=120)
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(
                merged=False,
                in_queue=False,
                auto_merge_enabled_at=enabled_at,
                merge_state_status="CLEAN",
            )
        )
        watcher._toggle_auto_merge = AsyncMock()  # type: ignore[method-assign]
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=1,
                target_branch="main",
                repo="owner/repo",
                stall_grace_period=60,
                max_stall_retries=1,
            )
        assert "stall_retries_attempted" in result, (
            "stalled response missing stall_retries_attempted"
        )
        assert result["stall_retries_attempted"] == 1, "stalled: expected max_stall_retries=1"
        assert result["pr_state"] == "stalled"

    @pytest.mark.anyio
    async def test_wait_accepts_stall_grace_period_and_max_stall_retries_params(self):
        """wait() accepts stall_grace_period and max_stall_retries without TypeError."""
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(merged=True)
        )
        result = await watcher.wait(
            pr_number=42,
            target_branch="main",
            repo="owner/repo",
            stall_grace_period=120,
            max_stall_retries=5,
        )
        assert result["success"] is True
        assert result["pr_state"] == "merged"


class TestPendingCIGuard:
    """Tests for the pending-CI guard that prevents false ejection when checks are running."""

    @pytest.mark.anyio
    async def test_continues_polling_when_checks_pending_and_not_in_queue(self):
        """Core bug: auto-merge + BLOCKED + PENDING checks must keep polling, not eject."""
        watcher = _make_watcher()
        call_count = 0

        async def _fetch(*_a, **_kw):
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                return _queue_state(
                    merged=False,
                    in_queue=False,
                    auto_merge_enabled_at=datetime.now(UTC),
                    merge_state_status="BLOCKED",
                    checks_state="PENDING",
                )
            return _queue_state(merged=True)

        watcher._fetch_pr_and_queue_state = _fetch  # type: ignore[method-assign]
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(pr_number=1, target_branch="main", repo="owner/repo")

        assert result["success"] is True
        assert result["pr_state"] == "merged"
        assert call_count >= 4

    @pytest.mark.anyio
    async def test_continues_polling_when_checks_expected_and_not_in_queue(self):
        """EXPECTED means 'check not yet started' — same as PENDING: keep polling."""
        watcher = _make_watcher()
        call_count = 0

        async def _fetch(*_a, **_kw):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return _queue_state(
                    in_queue=False,
                    auto_merge_enabled_at=datetime.now(UTC),
                    merge_state_status="BLOCKED",
                    checks_state="EXPECTED",
                )
            return _queue_state(merged=True)

        watcher._fetch_pr_and_queue_state = _fetch  # type: ignore[method-assign]
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(pr_number=1, target_branch="main", repo="owner/repo")

        assert result["success"] is True
        assert result["pr_state"] == "merged"
        assert call_count >= 3

    @pytest.mark.anyio
    async def test_returns_ejected_when_checks_terminal_and_not_in_queue(self):
        """checks_state=SUCCESS + not in queue → genuine ejection (guard must not block it)."""
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(
                in_queue=False,
                auto_merge_enabled_at=None,
                merge_state_status="BLOCKED",
                checks_state="SUCCESS",
            )
        )
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(pr_number=1, target_branch="main", repo="owner/repo")

        assert result["success"] is False
        assert result["pr_state"] == "ejected"

    @pytest.mark.anyio
    async def test_returns_ejected_when_no_status_checks_configured(self):
        """checks_state=None (no required checks) → ejected; unchanged for repos without CI."""
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(
                in_queue=False,
                merge_state_status="BLOCKED",
                checks_state=None,
            )
        )
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(pr_number=1, target_branch="main", repo="owner/repo")

        assert result["success"] is False
        assert result["pr_state"] == "ejected"

    @pytest.mark.anyio
    async def test_fetch_extracts_checks_state_from_graphql_response(self):
        """_fetch_pr_and_queue_state must extract statusCheckRollup.state into checks_state."""
        watcher = _make_watcher()

        async def _mock_post(url, *, json, headers=None, **_kw):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "id": "PR_kwDO_test",
                                "merged": False,
                                "state": "OPEN",
                                "mergeStateStatus": "BLOCKED",
                                "autoMergeRequest": {"enabledAt": "2026-03-26T12:00:00Z"},
                                "statusCheckRollup": {"state": "PENDING"},
                            },
                            "mergeQueue": {"entries": {"nodes": []}},
                        }
                    }
                },
                request=httpx.Request("POST", url),
            )

        watcher._client.post = _mock_post  # type: ignore[method-assign]
        state = await watcher._fetch_pr_and_queue_state(1, "owner", "repo", "main")

        assert state["checks_state"] == "PENDING"
        assert state["in_queue"] is False
        assert state["merged"] is False

    def test_implements_merge_queue_watcher_protocol(self):
        from autoskillit.core import MergeQueueWatcher

        assert isinstance(_make_watcher(), MergeQueueWatcher)


class TestRelatedCoverage:
    """Coverage for related untested paths found during investigation."""

    @pytest.mark.anyio
    async def test_returns_ejected_when_unmergeable_in_queue(self):
        """in_queue=True, queue_state=UNMERGEABLE → ejected immediately."""
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(in_queue=True, queue_state="UNMERGEABLE")
        )
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(pr_number=1, target_branch="main", repo="owner/repo")

        assert result["success"] is False
        assert result["pr_state"] == "ejected"

    @pytest.mark.anyio
    async def test_is_stall_candidate_when_has_hooks(self):
        """merge_state_status=HAS_HOOKS + auto-merge enabled → stall detection fires."""
        watcher = _make_watcher()
        enabled_at = datetime.now(UTC) - timedelta(seconds=120)
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(
                merged=False,
                in_queue=False,
                auto_merge_enabled_at=enabled_at,
                merge_state_status="HAS_HOOKS",
            )
        )
        watcher._toggle_auto_merge = AsyncMock()  # type: ignore[method-assign]
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=1,
                target_branch="main",
                repo="owner/repo",
                stall_grace_period=60,
                max_stall_retries=1,
            )

        assert result["pr_state"] == "stalled"
        assert watcher._toggle_auto_merge.call_count == 1  # type: ignore[union-attr]

    @pytest.mark.anyio
    async def test_returns_error_when_repo_has_no_slash(self):
        """repo='noslash' → pr_state='error', no polling."""
        watcher = _make_watcher()
        result = await watcher.wait(pr_number=1, target_branch="main", repo="noslash")

        assert result["success"] is False
        assert result["pr_state"] == "error"
        assert "Invalid repo format" in result["reason"]
