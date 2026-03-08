"""Tests for shared type contracts — enum exhaustiveness."""

import json

from autoskillit.core.types import (
    ChannelConfirmation,
    MergeFailedStep,
    MergeState,
    RestartScope,
    RetryReason,
    SessionOutcome,
    SkillResult,
)


def test_retry_reason_values():
    """RetryReason enum has exactly the expected members."""
    assert set(RetryReason) == {RetryReason.RESUME, RetryReason.NONE}
    assert RetryReason.NONE.value == "none"


def test_merge_failed_step_values():
    """MergeFailedStep enum covers all failure points."""
    assert set(MergeFailedStep) == {
        MergeFailedStep.PATH_VALIDATION,
        MergeFailedStep.BRANCH_DETECTION,
        MergeFailedStep.DIRTY_TREE,
        MergeFailedStep.TEST_GATE,
        MergeFailedStep.FETCH,
        MergeFailedStep.PRE_REBASE_CHECK,
        MergeFailedStep.MERGE_COMMITS_DETECTED,
        MergeFailedStep.REBASE,
        MergeFailedStep.GENERATED_FILE_CLEANUP,
        MergeFailedStep.POST_REBASE_TEST_GATE,
        MergeFailedStep.MERGE,
    }


def test_merge_state_values():
    """MergeState enum covers all repository states after failure."""
    assert set(MergeState) == {
        MergeState.WORKTREE_INTACT,
        MergeState.WORKTREE_INTACT_REBASE_ABORTED,
        MergeState.WORKTREE_INTACT_BASE_NOT_PUBLISHED,
        MergeState.WORKTREE_INTACT_MERGE_COMMITS_DETECTED,
        MergeState.WORKTREE_DIRTY,
        MergeState.WORKTREE_DIRTY_ABORT_FAILED,
        MergeState.WORKTREE_DIRTY_MID_OPERATION,
        MergeState.MAIN_REPO_MERGE_ABORTED,
        MergeState.MAIN_REPO_DIRTY_ABORT_FAILED,
    }


def test_merge_state_has_base_branch_not_published() -> None:
    """MergeState.WORKTREE_INTACT_BASE_NOT_PUBLISHED exists with correct value."""
    assert MergeState.WORKTREE_INTACT_BASE_NOT_PUBLISHED == "worktree_intact_base_not_published"


def test_restart_scope_values():
    """RestartScope enum covers both classification outcomes."""
    assert set(RestartScope) == {
        RestartScope.FULL_RESTART,
        RestartScope.PARTIAL_RESTART,
    }


def test_channel_confirmation_values():
    """ChannelConfirmation enum has exactly the expected members."""
    assert set(ChannelConfirmation) == {
        ChannelConfirmation.CHANNEL_A,
        ChannelConfirmation.CHANNEL_B,
        ChannelConfirmation.UNMONITORED,
    }
    assert ChannelConfirmation.CHANNEL_A.value == "channel_a"
    assert ChannelConfirmation.CHANNEL_B.value == "channel_b"
    assert ChannelConfirmation.UNMONITORED.value == "unmonitored"


def test_skill_command_prefix_constant_exists():
    """SKILL_COMMAND_PREFIX is the canonical slash prefix for skill invocations."""
    from autoskillit.core.types import SKILL_COMMAND_PREFIX

    assert SKILL_COMMAND_PREFIX == "/"


def test_autoskillit_skill_prefix_constant_exists():
    """AUTOSKILLIT_SKILL_PREFIX is the canonical prefix for bundled autoskillit skills."""
    from autoskillit.core.types import AUTOSKILLIT_SKILL_PREFIX

    assert AUTOSKILLIT_SKILL_PREFIX == "/autoskillit:"


# ---------------------------------------------------------------------------
# SessionOutcome enum tests
# ---------------------------------------------------------------------------


def test_session_outcome_is_str_enum_with_expected_values():
    """SessionOutcome inherits from StrEnum and has exactly three expected members."""
    from enum import StrEnum

    assert issubclass(SessionOutcome, StrEnum)
    assert set(SessionOutcome) == {
        SessionOutcome.SUCCEEDED,
        SessionOutcome.RETRIABLE,
        SessionOutcome.FAILED,
    }
    assert SessionOutcome.SUCCEEDED == "succeeded"
    assert SessionOutcome.RETRIABLE == "retriable"
    assert SessionOutcome.FAILED == "failed"


def test_skill_result_outcome_succeeded():
    """SkillResult with success=True, needs_retry=False → outcome is SUCCEEDED."""
    sr = SkillResult(
        success=True,
        result="ok",
        session_id="s1",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
    )
    assert sr.outcome is SessionOutcome.SUCCEEDED
    assert sr.outcome == "succeeded"


def test_skill_result_outcome_retriable():
    """SkillResult with success=False, needs_retry=True → outcome is RETRIABLE."""
    sr = SkillResult(
        success=False,
        result="partial",
        session_id="s1",
        subtype="error_max_turns",
        is_error=False,
        exit_code=1,
        needs_retry=True,
        retry_reason=RetryReason.RESUME,
        stderr="",
    )
    assert sr.outcome is SessionOutcome.RETRIABLE
    assert sr.outcome == "retriable"


def test_skill_result_outcome_failed():
    """SkillResult with success=False, needs_retry=False → outcome is FAILED."""
    sr = SkillResult(
        success=False,
        result="",
        session_id="s1",
        subtype="timeout",
        is_error=True,
        exit_code=-1,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
    )
    assert sr.outcome is SessionOutcome.FAILED
    assert sr.outcome == "failed"


def test_skill_result_to_json_excludes_outcome():
    """to_json() must not include 'outcome' — JSON contract is unchanged."""
    sr = SkillResult(
        success=True,
        result="ok",
        session_id="s1",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
    )
    parsed = json.loads(sr.to_json())
    assert "outcome" not in parsed


def test_session_outcome_accessible_from_core():
    """SessionOutcome is importable via the core package public surface."""
    from autoskillit.core import SessionOutcome as SO  # must not raise

    assert SO.SUCCEEDED == "succeeded"


def test_session_outcome_in_core_all():
    """SessionOutcome is listed in autoskillit.core.__all__."""
    import autoskillit.core as core_pkg

    assert "SessionOutcome" in core_pkg.__all__


def test_severity_has_ok_member():
    from autoskillit.core.types import Severity

    assert Severity.OK == "ok"
    assert Severity.ERROR == "error"
    assert Severity.WARNING == "warning"


def test_github_fetcher_protocol_has_label_methods():
    import inspect

    from autoskillit.core.types import GitHubFetcher

    members = {name for name, _ in inspect.getmembers(GitHubFetcher)}
    assert "add_labels" in members
    assert "remove_label" in members
    assert "ensure_label" in members
