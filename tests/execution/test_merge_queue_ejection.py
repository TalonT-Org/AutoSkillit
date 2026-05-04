"""Tests for merge queue ejection: RelatedCoverage, EjectionEnrichment,
FetchRepoMergeStateRetry, NotEnrolledState, and EnqueueMethod."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import autoskillit.execution.merge_queue as _mq
from autoskillit.core.types import PRState
from tests.execution.conftest import _make_watcher, _queue_state

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestRelatedCoverage:
    """Coverage for related untested paths found during investigation."""

    @pytest.mark.anyio
    async def test_unmergeable_waits_for_dequeue_before_returning_ejected(self):
        """UNMERGEABLE must wait for in_queue=False before returning EJECTED."""
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                _queue_state(in_queue=True, queue_state="UNMERGEABLE"),
                _queue_state(in_queue=True, queue_state="UNMERGEABLE"),
                _queue_state(in_queue=False, state="CLOSED"),
                _queue_state(in_queue=False, state="CLOSED"),
            ]
        )
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=1,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                not_in_queue_confirmation_cycles=2,
            )

        assert result["success"] is False
        assert result["pr_state"] == "ejected"
        assert watcher._fetch_pr_and_queue_state.call_count == 4  # type: ignore[union-attr]

    @pytest.mark.anyio
    async def test_unmergeable_dequeue_wait_respects_timeout(self):
        """UNMERGEABLE with perpetual in_queue=True times out instead of returning ejected."""
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(in_queue=True, queue_state="UNMERGEABLE")
        )
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=1,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                timeout_seconds=0,
            )

        assert result["success"] is False
        assert result["pr_state"] == "timeout"

    @pytest.mark.anyio
    async def test_unmergeable_respects_confirmation_cycles(self):
        """UNMERGEABLE with confirmation_cycles=2 requires 2 cycles after in_queue=False."""
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                _queue_state(in_queue=True, queue_state="UNMERGEABLE"),
                _queue_state(in_queue=False, state="CLOSED"),
                _queue_state(in_queue=False, state="CLOSED"),
            ]
        )
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=1,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                not_in_queue_confirmation_cycles=2,
            )

        assert result["success"] is False
        assert result["pr_state"] == "ejected"
        assert watcher._fetch_pr_and_queue_state.call_count == 3  # type: ignore[union-attr]

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
# fetch_repo_merge_state rate-limit retry
# ---------------------------------------------------------------------------


_SUCCESS_BODY = {
    "data": {
        "repository": {
            "mergeQueue": None,
            "autoMergeAllowed": False,
            "object": None,
        }
    }
}


class TestFetchRepoMergeStateRetry:
    """Tests for HTTP 429 / secondary-rate-limit 403 retry behaviour."""

    @pytest.mark.anyio
    async def test_retries_on_429(self, httpx_mock, monkeypatch):
        """Retries on HTTP 429, succeeds on second attempt."""
        sleep_calls: list[float] = []

        async def fake_sleep(secs: float) -> None:
            sleep_calls.append(secs)

        monkeypatch.setattr(_mq.asyncio, "sleep", fake_sleep)

        httpx_mock.add_response(
            url="https://api.github.com/graphql",
            status_code=429,
            headers={"Retry-After": "1"},
        )
        httpx_mock.add_response(
            url="https://api.github.com/graphql",
            json=_SUCCESS_BODY,
        )

        from autoskillit.execution.merge_queue import fetch_repo_merge_state

        result = await fetch_repo_merge_state(owner="o", repo="r", branch="main", token=None)
        assert result["queue_available"] is False
        assert len(sleep_calls) == 1
        assert sleep_calls[0] == 1.0  # Retry-After header value is '1'

    @pytest.mark.anyio
    async def test_retries_on_secondary_rate_limit_403(self, httpx_mock, monkeypatch):
        """Retries on 403 whose body contains 'secondary rate limit'."""
        sleep_calls: list[float] = []

        async def fake_sleep(secs: float) -> None:
            sleep_calls.append(secs)

        monkeypatch.setattr(_mq.asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(_mq.random, "uniform", lambda a, b: 0.42)

        httpx_mock.add_response(
            url="https://api.github.com/graphql",
            status_code=403,
            text="You have exceeded a secondary rate limit",
        )
        httpx_mock.add_response(
            url="https://api.github.com/graphql",
            json=_SUCCESS_BODY,
        )

        from autoskillit.execution.merge_queue import fetch_repo_merge_state

        result = await fetch_repo_merge_state(owner="o", repo="r", branch="main", token=None)
        assert result["queue_available"] is False
        assert len(sleep_calls) == 1
        assert sleep_calls[0] == 0.42  # jitter backoff via patched random.uniform

    @pytest.mark.anyio
    async def test_raises_on_non_rate_limit_403(self, httpx_mock):
        """Non-secondary-rate-limit 403 propagates immediately without retry."""
        httpx_mock.add_response(
            url="https://api.github.com/graphql",
            status_code=403,
            text="Bad credentials",
        )

        from autoskillit.execution.merge_queue import fetch_repo_merge_state

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await fetch_repo_merge_state(owner="o", repo="r", branch="main", token=None)
        assert exc_info.value.response.status_code == 403
        # Only one request was made (no retry)
        assert len(httpx_mock.get_requests()) == 1

    @pytest.mark.anyio
    async def test_exhausts_retries_and_raises(self, httpx_mock, monkeypatch):
        """After max_attempts of 429, raises HTTPStatusError."""
        sleep_calls: list[float] = []

        async def fake_sleep(secs: float) -> None:
            sleep_calls.append(secs)

        monkeypatch.setattr(_mq.asyncio, "sleep", fake_sleep)

        for _ in range(_mq._RATE_LIMIT_MAX_ATTEMPTS):
            httpx_mock.add_response(
                url="https://api.github.com/graphql",
                status_code=429,
                headers={"Retry-After": "1"},
            )

        from autoskillit.execution.merge_queue import fetch_repo_merge_state

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await fetch_repo_merge_state(owner="o", repo="r", branch="main", token=None)
        assert exc_info.value.response.status_code == 429
        assert len(httpx_mock.get_requests()) == _mq._RATE_LIMIT_MAX_ATTEMPTS


class TestNotEnrolledState:
    """Tests for the NOT_ENROLLED classifier state and enrollment tracking."""

    def test_pr_state_has_not_enrolled(self):
        """PRState must include NOT_ENROLLED value."""
        assert hasattr(PRState, "NOT_ENROLLED")
        assert PRState.NOT_ENROLLED == "not_enrolled"

    def test_classifier_returns_not_enrolled_when_never_in_queue_and_healthy(self):
        """A healthy PR with ever_enrolled=False must classify as NOT_ENROLLED."""
        state = _queue_state(
            merged=False,
            state="OPEN",
            mergeable="MERGEABLE",
            merge_state_status="CLEAN",
            checks_state="SUCCESS",
            in_queue=False,
            auto_merge_present=False,
        )
        result = _mq._classify_pr_state(state, ever_enrolled=False)
        assert result.terminal == PRState.NOT_ENROLLED

    def test_classifier_returns_dropped_healthy_only_when_ever_enrolled(self):
        """DROPPED_HEALTHY requires ever_enrolled=True."""
        state = _queue_state(
            merged=False,
            state="OPEN",
            mergeable="MERGEABLE",
            merge_state_status="CLEAN",
            checks_state="SUCCESS",
            in_queue=False,
            auto_merge_present=False,
        )
        result = _mq._classify_pr_state(state, ever_enrolled=True)
        assert result.terminal == PRState.DROPPED_HEALTHY

    @pytest.mark.anyio
    async def test_wait_tracks_ever_enrolled_from_in_queue(self):
        """wait() sets ever_enrolled=True when in_queue=True is observed."""
        watcher = _make_watcher()
        responses = [
            _queue_state(in_queue=True, queue_state="QUEUED"),
            _queue_state(
                in_queue=False,
                auto_merge_present=False,
                mergeable="MERGEABLE",
                merge_state_status="CLEAN",
                checks_state="SUCCESS",
            ),
            _queue_state(
                in_queue=False,
                auto_merge_present=False,
                mergeable="MERGEABLE",
                merge_state_status="CLEAN",
                checks_state="SUCCESS",
            ),
        ]
        watcher._fetch_pr_and_queue_state = AsyncMock(side_effect=responses)  # type: ignore[method-assign]
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                not_in_queue_confirmation_cycles=2,
            )
        assert result["pr_state"] == "dropped_healthy"

    @pytest.mark.anyio
    async def test_wait_tracks_ever_enrolled_from_auto_merge_present(self):
        """wait() sets ever_enrolled=True when auto_merge_present=True is observed."""
        watcher = _make_watcher()
        responses = [
            _queue_state(in_queue=False, auto_merge_present=True),
            _queue_state(
                in_queue=False,
                auto_merge_present=False,
                mergeable="MERGEABLE",
                merge_state_status="CLEAN",
                checks_state="SUCCESS",
            ),
            _queue_state(
                in_queue=False,
                auto_merge_present=False,
                mergeable="MERGEABLE",
                merge_state_status="CLEAN",
                checks_state="SUCCESS",
            ),
        ]
        watcher._fetch_pr_and_queue_state = AsyncMock(side_effect=responses)  # type: ignore[method-assign]
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                not_in_queue_confirmation_cycles=2,
            )
        assert result["pr_state"] == "dropped_healthy"

    @pytest.mark.anyio
    async def test_wait_returns_not_enrolled_when_enrollment_never_observed(self):
        """wait() returns NOT_ENROLLED when healthy PR never shows enrollment evidence."""
        watcher = _make_watcher()
        healthy_state = _queue_state(
            in_queue=False,
            auto_merge_present=False,
            mergeable="MERGEABLE",
            merge_state_status="CLEAN",
            checks_state="SUCCESS",
        )
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=healthy_state,
        )
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                not_in_queue_confirmation_cycles=2,
            )
        assert result["pr_state"] == "not_enrolled"
        assert result["success"] is False


# ---------------------------------------------------------------------------
# enqueue() method tests
# ---------------------------------------------------------------------------


class TestEnqueueMethod:
    """Tests for DefaultMergeQueueWatcher.enqueue() enrollment strategy."""

    @pytest.mark.anyio
    async def test_enqueue_uses_enqueue_pr_mutation_when_auto_merge_unavailable(self):
        """When auto_merge_available=False, enqueue() must call enqueuePullRequest."""
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(pr_node_id="PR_kwDO_test123"),
        )
        posted_bodies: list[str] = []

        async def _mock_post(*args, **kwargs):
            body = kwargs.get("json", {})
            posted_bodies.append(body.get("query", ""))
            return httpx.Response(
                200,
                json={"data": {"enqueuePullRequest": {"mergeQueueEntry": {"id": "MQE_1"}}}},
                request=httpx.Request("POST", "http://x"),
            )

        watcher._client.post = _mock_post  # type: ignore[method-assign]
        result = await watcher.enqueue(
            pr_number=42,
            target_branch="main",
            repo="owner/repo",
            auto_merge_available=False,
        )
        assert result["success"] is True
        assert result["enrollment_method"] == "direct_enqueue"
        assert any("enqueuePullRequest" in b for b in posted_bodies)
        assert not any("enablePullRequestAutoMerge" in b for b in posted_bodies)

    @pytest.mark.anyio
    async def test_enqueue_uses_auto_merge_mutation_when_auto_merge_available(self):
        """When auto_merge_available=True, enqueue() must call enablePullRequestAutoMerge."""
        watcher = _make_watcher()
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(pr_node_id="PR_kwDO_test123"),
        )
        posted_bodies: list[str] = []

        async def _mock_post(*args, **kwargs):
            body = kwargs.get("json", {})
            posted_bodies.append(body.get("query", ""))
            return httpx.Response(
                200,
                json={"data": {"enablePullRequestAutoMerge": {"pullRequest": {"number": 42}}}},
                request=httpx.Request("POST", "http://x"),
            )

        watcher._client.post = _mock_post  # type: ignore[method-assign]
        result = await watcher.enqueue(
            pr_number=42,
            target_branch="main",
            repo="owner/repo",
            auto_merge_available=True,
        )
        assert result["success"] is True
        assert result["enrollment_method"] == "auto_merge"
        assert any("enablePullRequestAutoMerge" in b for b in posted_bodies)
        assert not any("enqueuePullRequest" in b for b in posted_bodies)
