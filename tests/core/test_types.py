"""Tests for shared type contracts — enum exhaustiveness."""

import dataclasses
import json
from dataclasses import FrozenInstanceError
from typing import Any, ClassVar

import pytest

from autoskillit.core.types import (
    ChannelConfirmation,
    CIRunScope,
    InfraOutcome,
    MergeFailedStep,
    MergeState,
    ProviderOutcome,
    RestartScope,
    RetryReason,
    SessionOutcome,
    SkillResult,
    WriteEvidence,
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


def test_retry_reason_values():
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
    }
    assert RetryReason.NONE.value == "none"


def test_merge_failed_step_values():
    """MergeFailedStep enum covers all failure points."""
    assert set(MergeFailedStep) == {
        MergeFailedStep.PATH_VALIDATION,
        MergeFailedStep.PROTECTED_BRANCH,
        MergeFailedStep.BRANCH_DETECTION,
        MergeFailedStep.DIRTY_TREE,
        MergeFailedStep.DIRTY_MAIN_REPO,
        MergeFailedStep.TEST_GATE,
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


def test_merge_state_values():
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


@pytest.mark.parametrize(
    "kwargs, expected_outcome",
    [
        (
            dict(
                success=True,
                result="ok",
                session_id="s1",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            ),
            SessionOutcome.SUCCEEDED,
        ),
        (
            dict(
                success=False,
                result="partial",
                session_id="s1",
                subtype="error_max_turns",
                is_error=False,
                exit_code=1,
                needs_retry=True,
                retry_reason=RetryReason.RESUME,
                stderr="",
            ),
            SessionOutcome.RETRIABLE,
        ),
        (
            dict(
                success=False,
                result="",
                session_id="s1",
                subtype="timeout",
                is_error=True,
                exit_code=-1,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            ),
            SessionOutcome.FAILED,
        ),
    ],
    ids=["succeeded", "retriable", "failed"],
)
def test_skill_result_outcome(kwargs, expected_outcome):
    sr = SkillResult(**kwargs)
    assert sr.outcome is expected_outcome
    assert sr.outcome == expected_outcome.value


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
    assert Severity.INFO == "info"
    assert set(Severity) == {Severity.OK, Severity.ERROR, Severity.WARNING, Severity.INFO}


def test_severity_enum_not_equal_to_uppercase_string():
    """Regression: StrEnum values are lowercase; uppercase comparison is always False.

    ``f.severity == "ERROR"`` always returns False because Severity.ERROR.value
    is ``"error"`` (lowercase), not ``"ERROR"``.
    """
    from autoskillit.core.types import Severity

    assert Severity.ERROR != "ERROR"
    assert Severity.ERROR == "error"
    assert Severity.ERROR == Severity.ERROR


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


# ---------------------------------------------------------------------------
# P2-A6 — SubprocessRunner marker_dir and session_id params
# ---------------------------------------------------------------------------


def test_subprocess_runner_protocol_marker_dir_default_none():
    import inspect

    from autoskillit.core import SubprocessRunner

    sig = inspect.signature(SubprocessRunner.__call__)
    assert sig.parameters["marker_dir"].default is None


def test_subprocess_runner_protocol_session_id_default_none():
    import inspect

    from autoskillit.core import SubprocessRunner

    sig = inspect.signature(SubprocessRunner.__call__)
    assert sig.parameters["session_id"].default is None


def test_subprocess_runner_protocol_marker_params_after_max_extension():
    import inspect

    from autoskillit.core import SubprocessRunner

    sig = inspect.signature(SubprocessRunner.__call__)
    params = list(sig.parameters)
    max_ext_idx = params.index("max_extension_seconds")
    marker_idx = params.index("marker_dir")
    session_idx = params.index("session_id")
    assert marker_idx == max_ext_idx + 1, (
        f"marker_dir must immediately follow max_extension_seconds, "
        f"got indices {max_ext_idx} and {marker_idx}"
    )
    assert session_idx == marker_idx + 1, (
        f"session_id must immediately follow marker_dir, "
        f"got indices {marker_idx} and {session_idx}"
    )


def test_subprocess_runner_protocol_marker_params_are_keyword_only():
    import inspect

    from autoskillit.core import SubprocessRunner

    sig = inspect.signature(SubprocessRunner.__call__)
    for name in ("marker_dir", "session_id"):
        param = sig.parameters[name]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
            f"{name} must be keyword-only, got {param.kind.name}"
        )


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
        PRState.DROPPED_MERGE_GROUP_CI,
        PRState.NOT_ENROLLED,
        PRState.TIMEOUT,
        PRState.ERROR,
    }
    assert PRState.DROPPED_HEALTHY.value == "dropped_healthy"
    assert PRState.DROPPED_MERGE_GROUP_CI.value == "dropped_merge_group_ci"


class TestSkillResultCrashedFactory:
    def test_crashed_returns_skill_result_with_correct_fields(self):
        result = SkillResult.crashed(
            exception=RuntimeError("boom"),
            skill_command="/investigate test",
        )
        assert result.success is False
        assert result.subtype == "crashed"
        assert result.is_error is True
        assert result.exit_code == -1
        assert result.needs_retry is False
        assert result.retry_reason == RetryReason.NONE
        assert "RuntimeError: boom" in result.result
        assert result.session_id == ""
        assert result.stderr == ""

    def test_crashed_to_json_produces_valid_envelope(self):
        result = SkillResult.crashed(
            exception=RuntimeError("boom"),
            skill_command="/investigate test",
        )
        data = json.loads(result.to_json())
        assert "needs_retry" in data
        assert "session_id" in data
        assert "subtype" in data
        assert data["subtype"] == "crashed"

    def test_crashed_sets_provider_used_empty_string(self):
        result = SkillResult.crashed(exception=RuntimeError("boom"))
        assert result.provider.provider_used == ""

    def test_crashed_sets_provider_fallback_false(self):
        result = SkillResult.crashed(exception=RuntimeError("boom"))
        assert result.provider.fallback_activated is False


class TestSkillResultProviderFields:
    _BASE_KWARGS: ClassVar[dict[str, Any]] = {
        "success": True,
        "result": "ok",
        "session_id": "s1",
        "subtype": "success",
        "is_error": False,
        "exit_code": 0,
        "needs_retry": False,
        "retry_reason": RetryReason.NONE,
        "stderr": "",
    }

    def test_provider_used_defaults_to_empty_string(self):
        sr = SkillResult(**self._BASE_KWARGS)
        assert sr.provider.provider_used == ""

    def test_provider_fallback_defaults_to_false(self):
        sr = SkillResult(**self._BASE_KWARGS)
        assert sr.provider.fallback_activated is False

    def test_to_json_includes_provider_used_when_non_empty(self):
        sr = SkillResult(
            **self._BASE_KWARGS,
            provider=ProviderOutcome(provider_used="anthropic-vertex", fallback_activated=True),
        )
        data = json.loads(sr.to_json())
        assert data["provider_used"] == "anthropic-vertex"
        assert data["provider_fallback"] is True

    def test_to_json_includes_provider_used_as_empty_string_when_unset(self):
        sr = SkillResult(**self._BASE_KWARGS)
        data = json.loads(sr.to_json())
        assert "provider_used" in data
        assert data["provider_used"] == ""

    def test_to_json_includes_provider_fallback_when_true(self):
        sr = SkillResult(
            **self._BASE_KWARGS,
            provider=ProviderOutcome(provider_used="", fallback_activated=True),
        )
        data = json.loads(sr.to_json())
        assert "provider_fallback" in data
        assert data["provider_fallback"] is True

    def test_provider_used_round_trips_via_json(self):
        sr = SkillResult(
            **self._BASE_KWARGS,
            provider=ProviderOutcome(provider_used="bedrock-us", fallback_activated=False),
        )
        data = json.loads(sr.to_json())
        assert data["provider_used"] == "bedrock-us"


class TestInfraOutcome:
    def test_default_exit_category_is_empty(self):
        outcome = InfraOutcome()
        assert outcome.exit_category == ""

    def test_custom_exit_category(self):
        outcome = InfraOutcome(exit_category="context_exhausted")
        assert outcome.exit_category == "context_exhausted"

    def test_frozen_rejects_mutation(self):
        outcome = InfraOutcome(exit_category="completed")
        with pytest.raises(FrozenInstanceError):
            outcome.exit_category = "api_error"


class TestSkillResultExtensionBundles:
    _BASE_KWARGS: ClassVar[dict[str, Any]] = {
        "success": True,
        "result": "ok",
        "session_id": "",
        "subtype": "success",
        "is_error": False,
        "exit_code": 0,
        "needs_retry": False,
        "retry_reason": RetryReason.NONE,
        "stderr": "",
    }

    def test_provider_bundle_defaults_to_none_used(self):
        sr = SkillResult(**self._BASE_KWARGS)
        assert sr.provider == ProviderOutcome.none_used()
        assert sr.provider.provider_used == ""
        assert sr.provider.fallback_activated is False

    def test_infra_bundle_defaults_to_empty(self):
        sr = SkillResult(**self._BASE_KWARGS)
        assert sr.infra == InfraOutcome()
        assert sr.infra.exit_category == ""

    def test_provider_bundle_accepts_custom_value(self):
        sr = SkillResult(
            **self._BASE_KWARGS,
            provider=ProviderOutcome(provider_used="anthropic", fallback_activated=True),
        )
        assert sr.provider.provider_used == "anthropic"
        assert sr.provider.fallback_activated is True

    def test_infra_bundle_accepts_custom_value(self):
        sr = SkillResult(
            **self._BASE_KWARGS,
            infra=InfraOutcome(exit_category="api_error"),
        )
        assert sr.infra.exit_category == "api_error"

    def test_flat_provider_used_removed(self):
        assert "provider_used" not in [f.name for f in dataclasses.fields(SkillResult)]

    def test_flat_provider_fallback_removed(self):
        assert "provider_fallback" not in [f.name for f in dataclasses.fields(SkillResult)]

    def test_flat_infra_exit_category_removed(self):
        assert "infra_exit_category" not in [f.name for f in dataclasses.fields(SkillResult)]

    def test_to_json_emits_flat_provider_used_key(self):
        sr = SkillResult(
            **self._BASE_KWARGS,
            provider=ProviderOutcome(provider_used="vertex", fallback_activated=False),
        )
        data = json.loads(sr.to_json())
        assert data["provider_used"] == "vertex"

    def test_to_json_emits_flat_provider_fallback_key(self):
        sr = SkillResult(
            **self._BASE_KWARGS,
            provider=ProviderOutcome(provider_used="anthropic", fallback_activated=True),
        )
        data = json.loads(sr.to_json())
        assert data["provider_fallback"] is True

    def test_to_json_emits_flat_infra_exit_category_key(self):
        sr = SkillResult(
            **self._BASE_KWARGS,
            infra=InfraOutcome(exit_category="context_exhausted"),
        )
        data = json.loads(sr.to_json())
        assert data["infra_exit_category"] == "context_exhausted"

    def test_to_json_provider_empty_defaults(self):
        sr = SkillResult(**self._BASE_KWARGS)
        data = json.loads(sr.to_json())
        assert data["provider_used"] == ""
        assert data["provider_fallback"] is False
        assert data["infra_exit_category"] == ""

    def test_replace_infra_bundle(self):
        sr = SkillResult(**self._BASE_KWARGS)
        sr2 = dataclasses.replace(sr, infra=InfraOutcome(exit_category="api_error"))
        assert sr2.infra.exit_category == "api_error"
        assert sr.infra.exit_category == ""

    def test_replace_provider_bundle(self):
        sr = SkillResult(**self._BASE_KWARGS)
        sr2 = dataclasses.replace(
            sr, provider=ProviderOutcome(provider_used="vertex", fallback_activated=True)
        )
        assert sr2.provider.provider_used == "vertex"
        assert sr2.provider.fallback_activated is True


# ---------------------------------------------------------------------------
# T-ZW-6: git_writes_detected in has_progress_evidence
# ---------------------------------------------------------------------------


def test_git_writes_detected_in_has_progress_evidence() -> None:
    """SkillResult with git_writes_detected=True has has_progress_evidence=True
    even when worktree_path=None and write_call_count=0."""
    sr = SkillResult(
        success=True,
        result="done",
        session_id="s1",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
        worktree_path=None,
        evidence=WriteEvidence(
            fs_writes_detected=False, write_call_count=0, git_writes_detected=True
        ),
    )
    assert sr.has_progress_evidence is True


# T-DM-6
def test_skill_result_git_writes_detected_in_json() -> None:
    """to_json() must include git_writes_detected when True."""
    sr = SkillResult(
        success=True,
        result="done",
        session_id="s1",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
        evidence=WriteEvidence(
            write_call_count=0, fs_writes_detected=False, git_writes_detected=True
        ),
    )
    data = json.loads(sr.to_json())
    assert data["git_writes_detected"] is True


def test_skill_result_git_writes_detected_false_included() -> None:
    """to_json() unconditionally includes git_writes_detected (even when False)."""
    sr = SkillResult(
        success=True,
        result="done",
        session_id="s1",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
        evidence=WriteEvidence(
            write_call_count=0, fs_writes_detected=False, git_writes_detected=False
        ),
    )
    data = json.loads(sr.to_json())
    assert "git_writes_detected" in data
    assert data["git_writes_detected"] is False


def test_skill_result_file_changes_count_in_json() -> None:
    sr = SkillResult(
        success=True,
        result="done",
        session_id="s1",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
        evidence=WriteEvidence(
            write_call_count=0,
            fs_writes_detected=False,
            git_writes_detected=False,
            file_changes_count=2,
        ),
    )
    data = json.loads(sr.to_json())
    assert "file_changes_count" in data
    assert data["file_changes_count"] == 2
