"""Tests for core enum type contracts — exhaustive membership locks."""

from __future__ import annotations

from enum import StrEnum

import pytest

from autoskillit.core.types import (
    ChannelConfirmation,
    MergeFailedStep,
    MergeState,
    RestartScope,
    RetryReason,
    SessionOutcome,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


@pytest.mark.parametrize(
    ("raw", "expected_value"),
    [
        ("text", "text"),
        ("tool_use", "tool_use"),
        ("thinking", "thinking"),
        ("redacted_thinking", "redacted_thinking"),
        ("future_new_type", "unknown"),
        ("image", "image"),
        ("tool_result", "tool_result"),
    ],
)
def test_claude_content_block_type_from_api(raw: str, expected_value: str) -> None:
    from autoskillit.core.types import ClaudeContentBlockType

    block_type = ClaudeContentBlockType.from_api(raw)
    assert block_type.value == expected_value


def test_retry_reason_values() -> None:
    """RetryReason enum has exactly the expected members."""
    assert set(RetryReason) == {
        RetryReason.RESUME,
        RetryReason.NONE,
        RetryReason.BUDGET_EXHAUSTED,
        RetryReason.EARLY_STOP,
        RetryReason.ZERO_WRITES,
        RetryReason.EMPTY_OUTPUT,
        RetryReason.COMPLETED_NO_FLUSH,
        RetryReason.DRAIN_RACE,
        RetryReason.PATH_CONTAMINATION,
        RetryReason.CONTRACT_RECOVERY,
        RetryReason.STALE,
        RetryReason.CLONE_CONTAMINATION,
        RetryReason.THINKING_STALL,
        RetryReason.IDLE_STALL,
        RetryReason.RATE_LIMITED,
        RetryReason.CANCELLED,
        RetryReason.OUTCOME_INVARIANT,
        RetryReason.ASYNC_OBLIGATION,
    }
    assert RetryReason.NONE.value == "none"


def test_merge_failed_step_values() -> None:
    """MergeFailedStep enum covers all failure points."""
    assert set(MergeFailedStep) == {
        MergeFailedStep.PATH_VALIDATION,
        MergeFailedStep.PROTECTED_BRANCH,
        MergeFailedStep.BRANCH_DETECTION,
        MergeFailedStep.DIRTY_TREE,
        MergeFailedStep.DIRTY_MAIN_REPO,
        MergeFailedStep.TEST_GATE,
        MergeFailedStep.TEST_GATE_CONTENTION,
        MergeFailedStep.FETCH,
        MergeFailedStep.PRE_REBASE_CHECK,
        MergeFailedStep.MERGE_COMMITS_DETECTED,
        MergeFailedStep.REBASE,
        MergeFailedStep.GENERATED_FILE_CLEANUP,
        MergeFailedStep.POST_REBASE_TEST_GATE,
        MergeFailedStep.MERGE,
        MergeFailedStep.EDITABLE_INSTALL_GUARD,
        MergeFailedStep.EMBEDDED_WORKTREE,
        MergeFailedStep.REF_COHERENCE,
    }


def test_merge_state_values() -> None:
    """MergeState enum covers all repository states after failure."""
    assert set(MergeState) == {
        MergeState.WORKTREE_INTACT,
        MergeState.WORKTREE_INTACT_REBASE_ABORTED,
        MergeState.WORKTREE_INTACT_BASE_NOT_PUBLISHED,
        MergeState.WORKTREE_INTACT_MERGE_COMMITS_DETECTED,
        MergeState.WORKTREE_INTACT_REF_DIVERGED,
        MergeState.WORKTREE_DIRTY,
        MergeState.WORKTREE_DIRTY_ABORT_FAILED,
        MergeState.WORKTREE_DIRTY_MID_OPERATION,
        MergeState.MAIN_REPO_MERGE_ABORTED,
        MergeState.MAIN_REPO_DIRTY_ABORT_FAILED,
        MergeState.MERGE_SUCCEEDED_CLEANUP_BLOCKED,
    }


def test_restart_scope_values() -> None:
    """RestartScope enum covers both classification outcomes."""
    assert set(RestartScope) == {
        RestartScope.FULL_RESTART,
        RestartScope.PARTIAL_RESTART,
    }


def test_channel_confirmation_values() -> None:
    """ChannelConfirmation enum has exactly the expected members."""
    assert set(ChannelConfirmation) == {
        ChannelConfirmation.CHANNEL_A,
        ChannelConfirmation.CHANNEL_B,
        ChannelConfirmation.UNMONITORED,
        ChannelConfirmation.DIR_MISSING,
    }
    assert ChannelConfirmation.CHANNEL_A.value == "channel_a"
    assert ChannelConfirmation.CHANNEL_B.value == "channel_b"
    assert ChannelConfirmation.UNMONITORED.value == "unmonitored"
    assert ChannelConfirmation.DIR_MISSING.value == "dir_missing"


# ---------------------------------------------------------------------------
# SessionOutcome enum tests
# ---------------------------------------------------------------------------


def test_session_outcome_is_str_enum_with_expected_values() -> None:
    """SessionOutcome inherits from StrEnum and has exactly three expected members."""

    assert issubclass(SessionOutcome, StrEnum)
    assert set(SessionOutcome) == {
        SessionOutcome.SUCCEEDED,
        SessionOutcome.RETRIABLE,
        SessionOutcome.FAILED,
    }
    assert SessionOutcome.SUCCEEDED == "succeeded"
    assert SessionOutcome.RETRIABLE == "retriable"
    assert SessionOutcome.FAILED == "failed"


def test_session_outcome_accessible_from_core() -> None:
    """SessionOutcome is importable via the core package public surface."""
    from autoskillit.core import SessionOutcome as SO  # must not raise

    assert SO.SUCCEEDED == "succeeded"


def test_session_outcome_in_core_all() -> None:
    """SessionOutcome is listed in autoskillit.core.__all__."""
    import autoskillit.core as core_pkg

    assert "SessionOutcome" in core_pkg.__all__  # type: ignore[attr-defined]


def test_severity_has_ok_member() -> None:
    from autoskillit.core.types import Severity

    assert Severity.OK == "ok"
    assert Severity.ERROR == "error"
    assert Severity.WARNING == "warning"
    assert Severity.INFO == "info"
    assert set(Severity) == {Severity.OK, Severity.ERROR, Severity.WARNING, Severity.INFO}


def test_severity_enum_not_equal_to_uppercase_string() -> None:
    """Regression: StrEnum values are lowercase; uppercase comparison is always False.

    ``f.severity == "ERROR"`` always returns False because Severity.ERROR.value
    is ``"error"`` (lowercase), not ``"ERROR"``.
    """
    from autoskillit.core.types import Severity

    assert Severity.ERROR != "ERROR"
    assert Severity.ERROR == "error"
    assert Severity.ERROR == Severity.ERROR


def test_hook_trust_policy_values_and_public_exports() -> None:
    import autoskillit.core as core
    from autoskillit.core.types import HookTrustPolicy
    from autoskillit.core.types._type_enums import __all__ as enum_all

    assert issubclass(HookTrustPolicy, StrEnum)
    assert set(HookTrustPolicy) == {
        HookTrustPolicy.AUTOMATED,
        HookTrustPolicy.REVIEW_EACH_SESSION,
    }
    assert HookTrustPolicy.AUTOMATED.value == "automated"
    assert HookTrustPolicy.REVIEW_EACH_SESSION.value == "review_each_session"
    assert "HookTrustPolicy" in enum_all
    assert "HookTrustPolicy" in core.__all__  # type: ignore[attr-defined]
    assert core.HookTrustPolicy is HookTrustPolicy


def test_pr_state_enum_members_are_locked() -> None:
    """PRState enum has exactly the expected members — prevents silent addition/removal."""
    from autoskillit.core.types import PRState

    assert set(PRState) == {
        PRState.MERGED,
        PRState.EJECTED,
        PRState.EJECTED_CI_FAILURE,
        PRState.STALLED,
        PRState.DROPPED_HEALTHY,
        PRState.DROPPED_MERGE_GROUP_CI,
        PRState.NOT_ENROLLED,
        PRState.TIMEOUT,
        PRState.ERROR,
    }
    assert PRState.DROPPED_HEALTHY.value == "dropped_healthy"
    assert PRState.DROPPED_MERGE_GROUP_CI.value == "dropped_merge_group_ci"
