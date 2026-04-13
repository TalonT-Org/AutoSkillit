"""Tests for shared type contracts — enum exhaustiveness."""

import dataclasses
import json

import pytest

from autoskillit.core.types import (
    ChannelConfirmation,
    CIRunScope,
    MergeFailedStep,
    MergeState,
    RestartScope,
    RetryReason,
    SessionOutcome,
    SkillResult,
)


def test_retry_reason_values():
    """RetryReason enum has exactly the expected members."""
    assert set(RetryReason) == {
        RetryReason.RESUME,
        RetryReason.NONE,
        RetryReason.BUDGET_EXHAUSTED,
        RetryReason.EARLY_STOP,
        RetryReason.ZERO_WRITES,
        RetryReason.EMPTY_OUTPUT,
        RetryReason.DRAIN_RACE,
        RetryReason.PATH_CONTAMINATION,
        RetryReason.CONTRACT_RECOVERY,
        RetryReason.STALE,
        RetryReason.CLONE_CONTAMINATION,
    }
    assert RetryReason.NONE.value == "none"


def test_merge_failed_step_values():
    """MergeFailedStep enum covers all failure points."""
    assert set(MergeFailedStep) == {
        MergeFailedStep.PATH_VALIDATION,
        MergeFailedStep.PROTECTED_BRANCH,
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
        MergeFailedStep.EDITABLE_INSTALL_GUARD,
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
        MergeState.MERGE_SUCCEEDED_CLEANUP_BLOCKED,
    }


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
        ChannelConfirmation.DIR_MISSING,
    }
    assert ChannelConfirmation.CHANNEL_A.value == "channel_a"
    assert ChannelConfirmation.CHANNEL_B.value == "channel_b"
    assert ChannelConfirmation.UNMONITORED.value == "unmonitored"
    assert ChannelConfirmation.DIR_MISSING.value == "dir_missing"


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
    assert set(Severity) == {Severity.OK, Severity.ERROR, Severity.WARNING}


def test_github_fetcher_protocol_has_label_methods():
    import inspect

    from autoskillit.core.types import GitHubFetcher

    members = {name for name, _ in inspect.getmembers(GitHubFetcher)}
    assert "add_labels" in members
    assert "remove_label" in members
    assert "ensure_label" in members


def test_subprocess_result_has_elapsed_seconds_field():
    """SubprocessResult must carry a pre-computed monotonic elapsed_seconds."""
    from autoskillit.core.types import SubprocessResult, TerminationReason

    result = SubprocessResult(
        returncode=0,
        stdout="",
        stderr="",
        termination=TerminationReason.COMPLETED,
        pid=1,
    )
    assert hasattr(result, "elapsed_seconds")
    assert result.elapsed_seconds == 0.0
    result2 = dataclasses.replace(result, elapsed_seconds=7.3)
    assert result2.elapsed_seconds == pytest.approx(7.3)


# ---------------------------------------------------------------------------
# SkillResult.worktree_path field + to_json() conditional inclusion
# ---------------------------------------------------------------------------


def test_skill_result_to_json_includes_worktree_path_when_set():
    """worktree_path appears as a top-level JSON field when not None."""
    sr = SkillResult(
        success=False,
        result="Context limit reached during session execution.",
        session_id="s1",
        subtype="error_during_execution",
        is_error=True,
        exit_code=-1,
        needs_retry=True,
        retry_reason=RetryReason.RESUME,
        stderr="",
        worktree_path="/projects/worktrees/impl-fix-20260307",
    )
    data = json.loads(sr.to_json())
    assert data["worktree_path"] == "/projects/worktrees/impl-fix-20260307"


def test_skill_result_to_json_omits_worktree_path_when_none():
    """worktree_path key is absent from JSON when the field is None."""
    sr = SkillResult(
        success=True,
        result="Done.",
        session_id="s1",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
    )
    data = json.loads(sr.to_json())
    assert "worktree_path" not in data


# ---------------------------------------------------------------------------
# WriteBehaviorSpec and WriteExpectedResolver
# ---------------------------------------------------------------------------


def test_write_expected_skills_frozenset_removed() -> None:
    """WRITE_EXPECTED_SKILLS must not exist — replaced by contract-driven gate."""
    import autoskillit.core.types as types_mod

    assert not hasattr(types_mod, "WRITE_EXPECTED_SKILLS")


def test_write_behavior_spec_dataclass() -> None:
    """WriteBehaviorSpec must be importable with correct defaults."""
    from autoskillit.core import WriteBehaviorSpec

    default = WriteBehaviorSpec()
    assert default.mode is None
    assert default.expected_when == ()
    always = WriteBehaviorSpec(mode="always")
    assert always.mode == "always"
    cond = WriteBehaviorSpec(mode="conditional", expected_when=("pat",))
    assert cond.expected_when == ("pat",)


# ---------------------------------------------------------------------------
# P10-F1 — SubprocessRunner.pty_mode default
# ---------------------------------------------------------------------------


def test_subprocess_runner_protocol_pty_mode_default_false():
    import inspect

    from autoskillit.core import SubprocessRunner

    sig = inspect.signature(SubprocessRunner.__call__)
    assert sig.parameters["pty_mode"].default is False


def test_default_subprocess_runner_pty_mode_default_false():
    import inspect

    from autoskillit.execution.process import DefaultSubprocessRunner

    sig = inspect.signature(DefaultSubprocessRunner.__call__)
    assert sig.parameters["pty_mode"].default is False


def test_run_managed_async_pty_mode_default_false():
    import inspect

    from autoskillit.execution.process import run_managed_async

    sig = inspect.signature(run_managed_async)
    assert sig.parameters["pty_mode"].default is False


# ---------------------------------------------------------------------------
# CIRunScope event field
# ---------------------------------------------------------------------------


def test_ci_run_scope_event_field():
    """CIRunScope must accept and store an event field."""
    scope = CIRunScope(event="push")
    assert scope.event == "push"
    assert scope.workflow is None
    assert scope.head_sha is None


def test_ci_run_scope_event_defaults_to_none():
    """CIRunScope.event defaults to None when not specified."""
    scope = CIRunScope()
    assert scope.event is None


def test_pr_state_enum_members_are_locked():
    """PRState enum has exactly the expected members — prevents silent addition/removal."""
    from autoskillit.core.types import PRState

    assert set(PRState) == {
        PRState.MERGED,
        PRState.EJECTED,
        PRState.EJECTED_CI_FAILURE,
        PRState.STALLED,
        PRState.DROPPED_HEALTHY,
        PRState.TIMEOUT,
        PRState.ERROR,
    }
    assert PRState.DROPPED_HEALTHY.value == "dropped_healthy"
