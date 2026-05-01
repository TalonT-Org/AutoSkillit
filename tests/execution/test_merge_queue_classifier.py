"""Tests for merge queue classifier: PendingCIGuard, InconclusiveBudget,
ClassifierImmunity, and VocabularyContract."""

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
from tests.execution.conftest import _make_watcher, _queue_state

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


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
        Requires prior enrollment evidence (auto_merge_present=True in cycle 1).
        """
        watcher = _make_watcher()
        responses = [
            _queue_state(
                in_queue=False,
                mergeable="MERGEABLE",
                merge_state_status="CLEAN",
                checks_state="SUCCESS",
                auto_merge_present=True,
            ),
            _queue_state(
                in_queue=False,
                mergeable="MERGEABLE",
                merge_state_status="CLEAN",
                checks_state="SUCCESS",
                auto_merge_present=False,
            ),
            _queue_state(
                in_queue=False,
                mergeable="MERGEABLE",
                merge_state_status="CLEAN",
                checks_state="SUCCESS",
                auto_merge_present=False,
            ),
        ]
        watcher._fetch_pr_and_queue_state = AsyncMock(side_effect=responses)  # type: ignore[method-assign]
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


class TestInconclusiveBudget:
    """Tests for the split inconclusive budget: CIStillRunning vs NoPositiveSignal."""

    @pytest.mark.anyio
    async def test_pending_ci_does_not_exhaust_inconclusive_budget(self):
        """CIStillRunning must not consume inconclusive_count.
        Six PENDING cycles with budget=3 must NOT trigger budget ceiling.
        timeout_seconds=99999 ensures outer deadline is not the cause.
        """
        watcher = DefaultMergeQueueWatcher(token=None)
        call_count = 0

        async def _fetch(*_a, **_kw):
            nonlocal call_count
            call_count += 1
            if call_count > 6:
                return _queue_state(merged=True)
            return _queue_state(
                merged=False,
                in_queue=False,
                checks_state="PENDING",
                merge_state_status="BLOCKED",
                auto_merge_enabled_at=None,
            )

        watcher._fetch_pr_and_queue_state = _fetch  # type: ignore[method-assign]
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                timeout_seconds=99999,
            )

        assert result["success"] is True
        assert result["pr_state"] == "merged"

    @pytest.mark.anyio
    async def test_expected_ci_does_not_exhaust_inconclusive_budget(self):
        """CIStillRunning (EXPECTED) must not consume inconclusive_count.
        Six EXPECTED cycles with budget=3 must NOT trigger budget ceiling.
        """
        watcher = DefaultMergeQueueWatcher(token=None)
        call_count = 0

        async def _fetch(*_a, **_kw):
            nonlocal call_count
            call_count += 1
            if call_count > 6:
                return _queue_state(merged=True)
            return _queue_state(
                merged=False,
                in_queue=False,
                checks_state="EXPECTED",
                merge_state_status="BLOCKED",
                auto_merge_enabled_at=None,
            )

        watcher._fetch_pr_and_queue_state = _fetch  # type: ignore[method-assign]
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                timeout_seconds=99999,
            )

        assert result["success"] is True
        assert result["pr_state"] == "merged"

    @pytest.mark.anyio
    async def test_no_positive_signal_exhausts_budget_returns_timeout(self):
        """NoPositiveSignal must still exhaust the bounded budget.
        Unknown state with no positive classifier match × (window + N) cycles
        → pr_state='timeout'. Reason must contain 'Inconclusive after'.
        """
        watcher = DefaultMergeQueueWatcher(token=None)
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(
                merged=False,
                in_queue=False,
                checks_state="SUCCESS",
                mergeable="UNKNOWN",
                merge_state_status="UNKNOWN",
                auto_merge_enabled_at=None,
            )
        )
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                timeout_seconds=99999,
            )

        assert result["success"] is False
        assert result["pr_state"] == "timeout"
        assert "Inconclusive after" in result["reason"]

    @pytest.mark.anyio
    async def test_inconclusive_count_resets_on_queue_reentry(self):
        """inconclusive_count must reset when in_queue becomes True.
        Scenario: out(PENDING×5) → in_queue → out(PENDING×5) → merged.
        Budget=3. Without reset: second phase exhausts budget immediately.
        With reset: both phases are tolerated independently.
        """
        watcher = DefaultMergeQueueWatcher(token=None)
        call_count = 0

        async def _fetch(*_a, **_kw):
            nonlocal call_count
            call_count += 1
            if call_count <= 5:
                return _queue_state(
                    merged=False,
                    in_queue=False,
                    checks_state="PENDING",
                    merge_state_status="BLOCKED",
                    auto_merge_enabled_at=None,
                )
            if call_count == 6:
                return _queue_state(merged=False, in_queue=True)
            if call_count <= 11:
                return _queue_state(
                    merged=False,
                    in_queue=False,
                    checks_state="PENDING",
                    merge_state_status="BLOCKED",
                    auto_merge_enabled_at=None,
                )
            return _queue_state(merged=True)

        watcher._fetch_pr_and_queue_state = _fetch  # type: ignore[method-assign]
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                timeout_seconds=99999,
            )

        assert result["success"] is True
        assert result["pr_state"] == "merged"

    @pytest.mark.anyio
    async def test_confirmation_window_does_not_consume_inconclusive_budget(self):
        """CIStillRunning (PENDING) must never consume the inconclusive budget at any cycle.
        confirmation_cycles=4, budget=3: all 7 PENDING cycles complete without budget exhaustion
        because PENDING raises CIStillRunning (exempt from budget). Merged on call 8.
        """
        watcher = DefaultMergeQueueWatcher(token=None)
        call_count = 0

        async def _fetch(*_a, **_kw):
            nonlocal call_count
            call_count += 1
            if call_count > 7:
                return _queue_state(merged=True)
            return _queue_state(
                merged=False,
                in_queue=False,
                checks_state="PENDING",
                merge_state_status="BLOCKED",
                auto_merge_enabled_at=None,
            )

        watcher._fetch_pr_and_queue_state = _fetch  # type: ignore[method-assign]
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                timeout_seconds=99999,
                not_in_queue_confirmation_cycles=4,
            )

        assert result["success"] is True
        assert result["pr_state"] == "merged"
        assert call_count == 8

    @pytest.mark.anyio
    async def test_wait_accepts_max_inconclusive_retries_per_call_param(self):
        """max_inconclusive_retries must be a wait() parameter, not constructor-only.
        Passing it must not raise TypeError. Passing a small value (1) must cause
        budget exhaustion after 1 NoPositiveSignal cycle beyond the confirmation window.
        """
        watcher = _make_watcher()
        # State: unknown — no positive classifier fires → NoPositiveSignal
        watcher._fetch_pr_and_queue_state = AsyncMock(  # type: ignore[method-assign]
            return_value=_queue_state(
                merged=False,
                in_queue=False,
                checks_state="SUCCESS",
                mergeable="UNKNOWN",
                merge_state_status="UNKNOWN",
                auto_merge_enabled_at=None,
            )
        )
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                timeout_seconds=99999,
                max_inconclusive_retries=1,  # ← per-call override
            )

        assert result["success"] is False
        assert result["pr_state"] == "timeout"
        assert "Inconclusive after 1 retries" in result["reason"]

    @pytest.mark.anyio
    async def test_per_call_max_inconclusive_retries_overrides_constructor_default(self):
        """Per-call value must take precedence over the constructor default of 5.
        budget=2 per call, constructor default would be 5. Budget exhaustion must
        occur after 2 ambiguous cycles (+ confirmation window), not 5.
        """
        watcher = _make_watcher()  # constructor default = 5
        call_count = 0

        async def _fetch(*_a, **_kw):
            nonlocal call_count
            call_count += 1
            return _queue_state(
                merged=False,
                in_queue=False,
                checks_state="SUCCESS",
                mergeable="UNKNOWN",
                merge_state_status="UNKNOWN",
                auto_merge_enabled_at=None,
            )

        watcher._fetch_pr_and_queue_state = _fetch  # type: ignore[method-assign]
        with patch("autoskillit.execution.merge_queue.asyncio.sleep", new_callable=AsyncMock):
            result = await watcher.wait(
                pr_number=42,
                target_branch="main",
                repo="owner/repo",
                poll_interval=1,
                timeout_seconds=99999,
                max_inconclusive_retries=2,  # ← per-call override
            )

        assert result["success"] is False
        assert result["pr_state"] == "timeout"
        # confirmation_cycles=2 (default): cycle 1 is window, cycles 2-3 consume budget
        # budget=2: exhausted on call 3 → total calls = 3
        assert call_count == 3


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
        result = _mq._classify_pr_state(state, ever_enrolled=True)
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
            result = _mq._classify_pr_state(state, ever_enrolled=True)
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
            _mq._classify_pr_state(state, ever_enrolled=True)
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
                    merge_group_checks_state=None,
                ),
            ),
            (
                PRState.DROPPED_MERGE_GROUP_CI,
                _queue_state(
                    merged=False,
                    state="OPEN",
                    mergeable="MERGEABLE",
                    merge_state_status="CLEAN",
                    checks_state="SUCCESS",
                    auto_merge_present=False,
                    in_queue=False,
                    merge_group_checks_state="FAILURE",
                ),
            ),
        ],
    )
    def test_classifier_returns_expected_terminal_for_canonical_fixture(
        self, expected_terminal, state
    ):
        """Every classifier-reachable PRState terminal has at least one positive fixture."""
        result = _mq._classify_pr_state(state, ever_enrolled=True)
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


class TestMergeQueueVocabularyContract:
    """KNOWN_MQ_MERGE_STATE_STATUSES must be declared as a named frozenset constant."""

    def test_known_mq_merge_state_statuses_constant_exists(self):
        """KNOWN_MQ_MERGE_STATE_STATUSES must be exported as a module-level frozenset."""
        from autoskillit.execution import merge_queue

        assert hasattr(merge_queue, "KNOWN_MQ_MERGE_STATE_STATUSES")
        assert isinstance(merge_queue.KNOWN_MQ_MERGE_STATE_STATUSES, frozenset)
        assert "CLEAN" in merge_queue.KNOWN_MQ_MERGE_STATE_STATUSES

    def test_positive_stall_statuses_subset_of_known(self):
        """The positive-stall statuses used in _is_positive_stall must be known."""
        from autoskillit.execution.merge_queue import KNOWN_MQ_MERGE_STATE_STATUSES

        positive_stall_statuses = frozenset({"CLEAN", "HAS_HOOKS"})
        assert positive_stall_statuses.issubset(KNOWN_MQ_MERGE_STATE_STATUSES), (
            f"Positive stall statuses not in KNOWN_MQ_MERGE_STATE_STATUSES: "
            f"{positive_stall_statuses - KNOWN_MQ_MERGE_STATE_STATUSES}"
        )


class TestDroppedMergeGroupCI:
    """Tests for the DROPPED_MERGE_GROUP_CI classifier state (merge-group CI blind spot fix)."""

    def test_dropped_healthy_not_returned_when_merge_group_ci_failed(self):
        """When a PR exits the queue with PR-branch CI SUCCESS but merge-group CI FAILURE,
        the classifier must return DROPPED_MERGE_GROUP_CI, not DROPPED_HEALTHY."""
        state = _queue_state(
            merged=False,
            state="OPEN",
            mergeable="MERGEABLE",
            merge_state_status="CLEAN",
            auto_merge_present=False,
            in_queue=False,
            checks_state="SUCCESS",
            merge_group_checks_state="FAILURE",
        )
        result = _mq._classify_pr_state(state, ever_enrolled=True)
        assert result.terminal == PRState.DROPPED_MERGE_GROUP_CI
        assert result.terminal != PRState.DROPPED_HEALTHY

    def test_dropped_healthy_fires_when_merge_group_ci_unknown(self):
        """When merge-group CI result is unknown (None), DROPPED_HEALTHY is still valid —
        we cannot confirm the ejection was CI-caused, so the conservative classification
        (re-enroll) is correct."""
        state = _queue_state(
            merged=False,
            state="OPEN",
            mergeable="MERGEABLE",
            merge_state_status="CLEAN",
            auto_merge_present=False,
            in_queue=False,
            checks_state="SUCCESS",
            merge_group_checks_state=None,
        )
        result = _mq._classify_pr_state(state, ever_enrolled=True)
        assert result.terminal == PRState.DROPPED_HEALTHY

    def test_dropped_merge_group_ci_also_fires_on_error_conclusion(self):
        """merge_group_checks_state='ERROR' is treated the same as 'FAILURE'."""
        state = _queue_state(
            merged=False,
            state="OPEN",
            mergeable="MERGEABLE",
            merge_state_status="CLEAN",
            auto_merge_present=False,
            in_queue=False,
            checks_state="SUCCESS",
            merge_group_checks_state="ERROR",
        )
        result = _mq._classify_pr_state(state, ever_enrolled=True)
        assert result.terminal == PRState.DROPPED_MERGE_GROUP_CI
