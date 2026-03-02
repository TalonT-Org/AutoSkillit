"""Tests for shared type contracts — enum exhaustiveness."""

import json

from autoskillit.core.types import (
    RETRY_RESPONSE_FIELDS,
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
        MergeFailedStep.TEST_GATE,
        MergeFailedStep.FETCH,
        MergeFailedStep.REBASE,
        MergeFailedStep.POST_REBASE_TEST_GATE,
        MergeFailedStep.MERGE,
    }


def test_merge_state_values():
    """MergeState enum covers all repository states after failure."""
    assert set(MergeState) == {
        MergeState.WORKTREE_INTACT,
        MergeState.WORKTREE_INTACT_REBASE_ABORTED,
        MergeState.WORKTREE_INTACT_BASE_NOT_PUBLISHED,
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


# T6: RETRY_RESPONSE_FIELDS structural guard
def test_retry_response_fields_matches_to_json_output():
    """RETRY_RESPONSE_FIELDS must match the keys emitted by SkillResult.to_json().

    Structural guard: if a field is added to SkillResult without updating
    RETRY_RESPONSE_FIELDS, this test fails immediately, preventing silent
    drift in recipe step `retry.on` validation.
    """
    sr = SkillResult(
        success=True,
        result="",
        session_id="",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
    )
    json_keys = frozenset(json.loads(sr.to_json()).keys())
    assert RETRY_RESPONSE_FIELDS == json_keys, (
        f"RETRY_RESPONSE_FIELDS out of sync with SkillResult.to_json().\n"
        f"Missing: {json_keys - RETRY_RESPONSE_FIELDS}\n"
        f"Extra: {RETRY_RESPONSE_FIELDS - json_keys}"
    )


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


def test_retry_response_fields_excludes_outcome():
    """RETRY_RESPONSE_FIELDS must not include 'outcome' after adding the @property."""
    assert "outcome" not in RETRY_RESPONSE_FIELDS


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
