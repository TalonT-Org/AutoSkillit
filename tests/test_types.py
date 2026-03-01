"""Tests for shared type contracts — enum exhaustiveness."""

import json
from dataclasses import fields

from autoskillit.core.types import (
    RETRY_RESPONSE_FIELDS,
    ChannelConfirmation,
    MergeFailedStep,
    MergeState,
    RestartScope,
    RetryReason,
    SkillResult,
)


def test_retry_reason_values():
    """RetryReason enum has exactly the expected members."""
    assert set(RetryReason) == {RetryReason.RESUME, RetryReason.NONE}
    assert RetryReason.NONE.value == "none"


def test_merge_failed_step_values():
    """MergeFailedStep enum covers all failure points."""
    assert set(MergeFailedStep) == {
        MergeFailedStep.TEST_GATE,
        MergeFailedStep.FETCH,
        MergeFailedStep.REBASE,
        MergeFailedStep.MERGE,
    }


def test_merge_state_values():
    """MergeState enum covers all repository states after failure."""
    assert set(MergeState) == {
        MergeState.WORKTREE_INTACT,
        MergeState.WORKTREE_INTACT_REBASE_ABORTED,
        MergeState.WORKTREE_INTACT_BASE_NOT_PUBLISHED,
        MergeState.MAIN_REPO_MERGE_ABORTED,
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


def test_retry_response_fields_derived_from_skillresult_fields():
    """RETRY_RESPONSE_FIELDS must equal the field names of SkillResult dataclass.

    After I9, this is structurally enforced — but this test makes the
    derivation contract explicit and catches any divergence.
    """
    dataclass_fields = frozenset(f.name for f in fields(SkillResult))
    assert RETRY_RESPONSE_FIELDS == dataclass_fields, (
        f"RETRY_RESPONSE_FIELDS not derived from SkillResult fields.\n"
        f"Missing: {dataclass_fields - RETRY_RESPONSE_FIELDS}\n"
        f"Extra: {RETRY_RESPONSE_FIELDS - dataclass_fields}"
    )


def test_skill_command_prefix_constant_exists():
    """SKILL_COMMAND_PREFIX is the canonical slash prefix for skill invocations."""
    from autoskillit.core.types import SKILL_COMMAND_PREFIX

    assert SKILL_COMMAND_PREFIX == "/"


def test_autoskillit_skill_prefix_constant_exists():
    """AUTOSKILLIT_SKILL_PREFIX is the canonical prefix for bundled autoskillit skills."""
    from autoskillit.core.types import AUTOSKILLIT_SKILL_PREFIX

    assert AUTOSKILLIT_SKILL_PREFIX == "/autoskillit:"
