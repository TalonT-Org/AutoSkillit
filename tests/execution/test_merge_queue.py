"""Tests for DefaultMergeQueueWatcher polling state machine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import autoskillit.execution.merge_queue as _mq
from autoskillit.core.types import PRState
from autoskillit.execution.merge_queue import (
    ClassifierInconclusive,
    DefaultMergeQueueWatcher,
    PRFetchState,
)


def _make_watcher() -> DefaultMergeQueueWatcher:
    return DefaultMergeQueueWatcher(token=None)


def _queue_state(
    *,
    merged: bool = False,
    state: str = "OPEN",
    mergeable: str = "MERGEABLE",
    merge_state_status: str = "CLEAN",
    auto_merge_present: bool = False,
    auto_merge_enabled_at: datetime | None = None,
    pr_node_id: str = "PR_kwDO_test",
    in_queue: bool = False,
    queue_state: str | None = None,
    checks_state: str | None = None,
) -> PRFetchState:
    return {
        "merged": merged,
        "state": state,
        "mergeable": mergeable,
        "merge_state_status": merge_state_status,
        "auto_merge_present": auto_merge_present,
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
    async def test_returns_ejected_when_mergeable_conflicting_and_not_in_queue(self):
        """mergeable=CONFLICTING + not in queue → ejected via positive conflicting signal."""
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(
                merged=False,
                in_queue=False,
                mergeable="CONFLICTING",
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
        """Two consecutive not-in-queue cycles required before acting on CONFLICTING ejection."""
        watcher = _make_watcher()
        state = _queue_state(
            merged=False, in_queue=False, mergeable="CONFLICTING", merge_state_status="BLOCKED"
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
    async def test_returns_dropped_healthy_when_checks_success_and_mergeable(self):
        """Healthy PR (SUCCESS + MERGEABLE + CLEAN + auto_merge cleared) → dropped_healthy.

        This is the exact issue #802 failure mode: the old elimination classifier returned
        'ejected' for this state because no other gate matched. The new classifier returns
        DROPPED_HEALTHY via positive signal — auto_merge was cleared while the PR was healthy.
        """
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(
                in_queue=False,
                mergeable="MERGEABLE",
                merge_state_status="CLEAN",
                checks_state="SUCCESS",
                auto_merge_present=False,
            )
        )
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(pr_number=1, target_branch="main", repo="owner/repo")

        assert result["success"] is False
        assert result["pr_state"] == "dropped_healthy"

    @pytest.mark.anyio
    async def test_returns_ejected_when_checks_success_but_mergeable_conflicting(self):
        """checks_state=SUCCESS + mergeable=CONFLICTING → ejected via positive CONFLICTING gate."""
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(
                in_queue=False,
                mergeable="CONFLICTING",
                merge_state_status="BLOCKED",
                checks_state="SUCCESS",
            )
        )
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(pr_number=1, target_branch="main", repo="owner/repo")

        assert result["success"] is False
        assert result["pr_state"] == "ejected"

    @pytest.mark.anyio
    async def test_returns_ejected_when_no_checks_configured_but_conflicting(self):
        """checks_state=None + mergeable=CONFLICTING → ejected via positive CONFLICTING signal.

        For repos without required CI checks, ejection requires a positive mergeable=CONFLICTING
        signal rather than the absence of checks. ClassifierInconclusive handles the ambiguous
        case (no CI, no conflict signal) by continuing to poll.
        """
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(
                in_queue=False,
                mergeable="CONFLICTING",
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


class TestEjectionEnrichment:
    """Tests ejection response enrichment with CI failure cause."""

    @pytest.mark.anyio
    async def test_ejected_ci_failure_when_checks_state_is_failure(self):
        """When checks_state=FAILURE, wait() returns pr_state='ejected_ci_failure'."""
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(
                merged=False,
                in_queue=False,
                checks_state="FAILURE",
                merge_state_status="BLOCKED",
                auto_merge_enabled_at=None,
            )
        )
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                not_in_queue_confirmation_cycles=2,
            )
        assert result["success"] is False
        assert result["pr_state"] == "ejected_ci_failure"
        assert result.get("ejection_cause") == "ci_failure"

    @pytest.mark.anyio
    async def test_ejected_when_checks_state_is_none(self):
        """checks_state=None + mergeable=CONFLICTING → pr_state='ejected', no ejection_cause."""
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(
                merged=False,
                in_queue=False,
                checks_state=None,
                merge_state_status="BLOCKED",
                mergeable="CONFLICTING",
                auto_merge_enabled_at=None,
            )
        )
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                not_in_queue_confirmation_cycles=2,
            )
        assert result["success"] is False
        assert result["pr_state"] == "ejected"
        assert "ejection_cause" not in result

    @pytest.mark.anyio
    async def test_ejected_when_checks_state_is_success(self):
        """checks_state=SUCCESS + mergeable=CONFLICTING → pr_state='ejected', no enrichment."""
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(
                merged=False,
                in_queue=False,
                checks_state="SUCCESS",
                merge_state_status="BLOCKED",
                mergeable="CONFLICTING",
                auto_merge_enabled_at=None,
            )
        )
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                not_in_queue_confirmation_cycles=2,
            )
        assert result["success"] is False
        assert result["pr_state"] == "ejected"
        assert "ejection_cause" not in result

    @pytest.mark.anyio
    async def test_ejected_ci_failure_on_closed_pr_with_failure_checks(self):
        """CLOSED state with checks_state=FAILURE → ejected_ci_failure."""
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(
                state="CLOSED",
                merged=False,
                checks_state="FAILURE",
            )
        )
        result = await watcher.wait(
            pr_number=42, target_branch="main", repo="owner/repo", poll_interval=1
        )
        assert result["success"] is False
        assert result["pr_state"] == "ejected_ci_failure"
        assert result.get("ejection_cause") == "ci_failure"

    @pytest.mark.anyio
    async def test_ejected_ci_failure_after_in_queue_to_not_in_queue_transition(self):
        """in_queue=True on first poll, then in_queue=False+FAILURE → ejected_ci_failure."""
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                _queue_state(in_queue=True, checks_state=None),
                _queue_state(
                    in_queue=False,
                    checks_state="FAILURE",
                    merge_state_status="BLOCKED",
                    auto_merge_enabled_at=None,
                ),
                _queue_state(
                    in_queue=False,
                    checks_state="FAILURE",
                    merge_state_status="BLOCKED",
                    auto_merge_enabled_at=None,
                ),
            ]
        )
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                not_in_queue_confirmation_cycles=2,
            )
        assert result["success"] is False
        assert result["pr_state"] == "ejected_ci_failure"
        assert result.get("ejection_cause") == "ci_failure"


# ---------------------------------------------------------------------------
# Part A: New classifier immunity tests (T1–T6)
# ---------------------------------------------------------------------------


class TestClassifierImmunity:
    """Positive-signal immunity tests for the extracted _classify_pr_state function."""

    # --- T1: Reproduces the reported bug ---

    def test_classifier_returns_dropped_healthy_when_auto_merge_cleared_on_healthy_pr(self):
        """Exact issue #802 state: healthy PR with auto_merge cleared → DROPPED_HEALTHY."""
        state = _queue_state(
            merged=False,
            state="OPEN",
            mergeable="MERGEABLE",
            merge_state_status="CLEAN",
            checks_state="SUCCESS",
            in_queue=False,
            auto_merge_present=False,
        )
        result = _mq._classify_pr_state(state)
        assert result.terminal == PRState.DROPPED_HEALTHY

    # --- T2: Positive-signal contract: EJECTED requires positive signal ---

    @pytest.mark.parametrize(
        "state",
        [
            # open + MERGEABLE + CLEAN + PENDING checks
            _queue_state(
                mergeable="MERGEABLE",
                merge_state_status="CLEAN",
                checks_state="PENDING",
                auto_merge_present=True,
            ),
            # open + UNKNOWN mergeable + CLEAN + SUCCESS
            _queue_state(
                mergeable="UNKNOWN",
                merge_state_status="CLEAN",
                checks_state="SUCCESS",
            ),
            # open + MERGEABLE + HAS_HOOKS + SUCCESS + auto_merge_present=True
            _queue_state(
                mergeable="MERGEABLE",
                merge_state_status="HAS_HOOKS",
                checks_state="SUCCESS",
                auto_merge_present=True,
            ),
        ],
    )
    def test_classifier_never_returns_ejected_without_positive_signal(self, state):
        """For ambiguous-but-healthy states, classifier must NOT return EJECTED."""
        try:
            result = _mq._classify_pr_state(state)
            assert result.terminal != PRState.EJECTED, (
                f"Classifier returned EJECTED without a positive signal for state: {state}"
            )
        except ClassifierInconclusive:
            pass  # expected — no positive signal matched

    # --- T3: Classifier raises ClassifierInconclusive — no silent fall-through ---

    def test_classifier_raises_inconclusive_when_no_positive_signal_matches(self):
        """ClassifierInconclusive raised when no positive gate matches; .state exposes fields."""
        state = _queue_state(
            merged=False,
            state="OPEN",
            mergeable="UNKNOWN",
            merge_state_status="BEHIND",
            checks_state=None,
            auto_merge_present=True,
            in_queue=False,
        )
        with pytest.raises(ClassifierInconclusive) as exc_info:
            _mq._classify_pr_state(state)
        assert exc_info.value.state is state
        assert exc_info.value.reason

    # --- T4: Exhaustive PRState coverage ---

    @pytest.mark.parametrize(
        "expected_terminal,state",
        [
            (
                PRState.MERGED,
                _queue_state(merged=True),
            ),
            (
                PRState.EJECTED,
                _queue_state(
                    merged=False,
                    state="OPEN",
                    mergeable="CONFLICTING",
                    in_queue=False,
                ),
            ),
            (
                PRState.EJECTED_CI_FAILURE,
                _queue_state(
                    merged=False,
                    state="OPEN",
                    checks_state="FAILURE",
                    in_queue=False,
                ),
            ),
            (
                PRState.STALLED,
                _queue_state(
                    merged=False,
                    state="OPEN",
                    mergeable="MERGEABLE",
                    merge_state_status="CLEAN",
                    auto_merge_enabled_at=datetime.now(UTC) - timedelta(seconds=120),
                    auto_merge_present=True,
                    in_queue=False,
                ),
            ),
            (
                PRState.DROPPED_HEALTHY,
                _queue_state(
                    merged=False,
                    state="OPEN",
                    mergeable="MERGEABLE",
                    merge_state_status="CLEAN",
                    checks_state="SUCCESS",
                    auto_merge_present=False,
                    in_queue=False,
                ),
            ),
        ],
    )
    def test_classifier_returns_expected_terminal_for_canonical_fixture(
        self, expected_terminal, state
    ):
        """Every classifier-reachable PRState terminal has at least one positive fixture."""
        result = _mq._classify_pr_state(state)
        assert result.terminal == expected_terminal

    # --- T5: Import-time schema round-trip guard ---

    def test_query_field_map_matches_prfetchstate_required_keys(self):
        """_QUERY_FIELD_MAP keys must exactly match PRFetchState required+optional keys."""
        all_keys = PRFetchState.__required_keys__ | PRFetchState.__optional_keys__
        assert set(_mq._QUERY_FIELD_MAP) == all_keys, (
            f"Mismatch — missing from map: {all_keys - set(_mq._QUERY_FIELD_MAP)}, "
            f"extra in map: {set(_mq._QUERY_FIELD_MAP) - all_keys}"
        )

    # --- T6: Fixture immunity ---

    def test_queue_state_fixture_populates_all_prfetchstate_fields(self):
        """_queue_state() must cover every PRFetchState key so new fields are caught at once."""
        all_keys = PRFetchState.__required_keys__ | PRFetchState.__optional_keys__
        fixture_keys = set(_queue_state().keys())
        assert fixture_keys == all_keys, (
            f"_queue_state() missing keys: {all_keys - fixture_keys}, "
            f"extra keys: {fixture_keys - all_keys}"
        )
