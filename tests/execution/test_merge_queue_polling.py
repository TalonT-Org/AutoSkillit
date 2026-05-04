"""Tests for DefaultMergeQueueWatcher polling state machine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tests.execution.conftest import _make_watcher, _queue_state

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


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

    @pytest.mark.anyio
    async def test_transition_gate_queries_merge_group_ci_on_in_to_out_with_success_checks(self):
        """When the watcher observes in_queue True→False with checks_state=SUCCESS,
        it must call _query_merge_group_ci and surface DROPPED_MERGE_GROUP_CI if CI failed."""
        import autoskillit.execution.merge_queue as _mq

        watcher = _make_watcher()
        call_count = 0

        async def _fetch(*_a, **_kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _queue_state(in_queue=True, checks_state="SUCCESS")
            return _queue_state(
                in_queue=False,
                checks_state="SUCCESS",
                state="OPEN",
                mergeable="MERGEABLE",
                merge_state_status="CLEAN",
                auto_merge_present=False,
                merge_group_checks_state=None,
            )

        watcher._fetch_pr_and_queue_state = _fetch  # type: ignore[method-assign]
        with (
            patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock),
            patch.object(
                _mq, "_query_merge_group_ci", new_callable=AsyncMock, return_value="FAILURE"
            ) as mock_query,
        ):
            result = await watcher.wait(pr_number=7, target_branch="main", repo="owner/repo")

        mock_query.assert_called_once()
        assert result["pr_state"] == "dropped_merge_group_ci"

    @pytest.mark.anyio
    async def test_transition_gate_not_triggered_when_checks_already_failed(self):
        """When checks_state is FAILURE on exit, the transition gate must NOT query
        merge-group CI — the classifier already handles this as EJECTED_CI_FAILURE."""
        import autoskillit.execution.merge_queue as _mq

        watcher = _make_watcher()
        call_count = 0

        async def _fetch(*_a, **_kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _queue_state(in_queue=True, checks_state="SUCCESS")
            return _queue_state(in_queue=False, checks_state="FAILURE", state="OPEN")

        watcher._fetch_pr_and_queue_state = _fetch  # type: ignore[method-assign]
        with (
            patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock),
            patch.object(
                _mq, "_query_merge_group_ci", new_callable=AsyncMock, return_value="FAILURE"
            ) as mock_query,
        ):
            await watcher.wait(pr_number=7, target_branch="main", repo="owner/repo")

        mock_query.assert_not_called()


class TestToggleAutoMergeConfirmation:
    """Tests for _toggle_auto_merge confirmation polling instead of hardcoded sleep."""

    @pytest.mark.anyio
    async def test_toggle_polls_for_disable_confirmation(self):
        """_toggle_auto_merge polls until auto-merge is confirmed disabled."""
        watcher = _make_watcher()
        watcher._client = AsyncMock()

        disable_resp = MagicMock()
        disable_resp.status_code = 200
        disable_resp.raise_for_status = lambda: None
        disable_resp.json.return_value = {"data": {"disablePullRequestAutoMerge": {}}}

        poll_resp_still_active = MagicMock()
        poll_resp_still_active.status_code = 200
        poll_resp_still_active.json.return_value = {
            "data": {"node": {"autoMergeRequest": {"enabledAt": "2026-01-01T00:00:00Z"}}}
        }

        poll_resp_disabled = MagicMock()
        poll_resp_disabled.status_code = 200
        poll_resp_disabled.json.return_value = {"data": {"node": {"autoMergeRequest": None}}}

        enable_resp = MagicMock()
        enable_resp.status_code = 200
        enable_resp.raise_for_status = lambda: None
        enable_resp.json.return_value = {"data": {"enablePullRequestAutoMerge": {}}}

        watcher._client.post = AsyncMock(
            side_effect=[disable_resp, poll_resp_still_active, poll_resp_disabled, enable_resp]
        )
        watcher._ensure_client = lambda: watcher._client

        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            await watcher._toggle_auto_merge("PR_node123")

        assert watcher._client.post.call_count == 4

    @pytest.mark.anyio
    async def test_toggle_does_not_use_hardcoded_sleep_2(self):
        """_toggle_auto_merge no longer uses asyncio.sleep(2)."""
        watcher = _make_watcher()
        watcher._client = AsyncMock()

        disable_resp = MagicMock()
        disable_resp.status_code = 200
        disable_resp.raise_for_status = lambda: None
        disable_resp.json.return_value = {"data": {"disablePullRequestAutoMerge": {}}}

        poll_resp = MagicMock()
        poll_resp.status_code = 200
        poll_resp.json.return_value = {"data": {"node": {"autoMergeRequest": None}}}

        enable_resp = MagicMock()
        enable_resp.status_code = 200
        enable_resp.raise_for_status = lambda: None
        enable_resp.json.return_value = {"data": {"enablePullRequestAutoMerge": {}}}

        watcher._client.post = AsyncMock(side_effect=[disable_resp, poll_resp, enable_resp])
        watcher._ensure_client = lambda: watcher._client

        sleep_durations: list[float] = []

        async def _capture_sleep(seconds: float) -> None:
            sleep_durations.append(seconds)

        with patch("autoskillit.execution.merge_queue.asyncio.sleep", side_effect=_capture_sleep):
            await watcher._toggle_auto_merge("PR_node123")

        assert 2 not in sleep_durations
