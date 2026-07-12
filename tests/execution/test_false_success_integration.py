"""Integration tests for completion_required guard against false success bypass."""

from __future__ import annotations

import dataclasses
import json

import pytest

from autoskillit.core.types import (
    CleanupOutcome,
    CliSubtype,
    LifecycleDecision,
    RetryReason,
    SessionOutcome,
    TerminationReason,
    WriteBehaviorSpec,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.headless._headless_result import _build_skill_result
from autoskillit.execution.session import ClaudeSessionResult
from autoskillit.execution.session._session_outcome import _compute_outcome
from tests.execution.conftest import _sr

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small, pytest.mark.feature("fleet")]


def test_worktree_only_session_with_returncode_0_and_completion_required_fails():
    result_text = "worktree_path = /tmp/worktrees/impl-foo\nbranch_name = impl/foo"
    stdout = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": result_text,
            "session_id": "s1",
        }
    )
    proc_result = _sr(0, stdout, "", TerminationReason.NATURAL_EXIT)
    sr = _build_skill_result(
        proc_result,
        completion_marker="%%ORDER_UP::abc123%%",
        expected_output_patterns=[r"worktree_path[ \t]*=[ \t]*/.+"],
        write_behavior=WriteBehaviorSpec(mode="always"),
        completion_required=True,
        fs_writes_detected=True,
        backend=ClaudeCodeBackend(),
    )
    assert sr.success is False


def test_completion_required_blocks_prior_marker_bypass():
    result_text = "worktree_path = /tmp/worktrees/impl-foo\n%%ORDER_UP::old1234%%"
    session = ClaudeSessionResult(
        subtype=CliSubtype.SUCCESS,
        is_error=False,
        result=result_text,
        session_id="s1",
    )
    outcome, _retry_reason = _compute_outcome(
        session,
        0,
        TerminationReason.NATURAL_EXIT,
        completion_marker="%%ORDER_UP::new5678%%",
        prior_completion_markers=["%%ORDER_UP::old1234%%"],
        expected_output_patterns=[r"worktree_path[ \t]*=[ \t]*/.+"],
        completion_required=True,
    )
    assert outcome != SessionOutcome.SUCCEEDED


def test_completion_required_does_not_suppress_early_stop_retry():
    session = ClaudeSessionResult(
        subtype=CliSubtype.SUCCESS,
        is_error=False,
        result="worktree_path = /tmp/foo\nbranch_name = bar",
        session_id="s1",
    )
    outcome, retry_reason = _compute_outcome(
        session,
        0,
        TerminationReason.NATURAL_EXIT,
        completion_marker="%%ORDER_UP::abc%%",
        expected_output_patterns=[r"worktree_path[ \t]*=[ \t]*/.+"],
        completion_required=True,
    )
    assert outcome == SessionOutcome.RETRIABLE
    assert retry_reason == RetryReason.EARLY_STOP


def test_completion_required_blocks_normalized_subtype_upgrade():
    result_text = "worktree_path = /tmp/worktrees/impl-foo\nbranch_name = impl/foo"
    stdout = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": result_text,
            "session_id": "s1",
        }
    )
    proc_result = _sr(0, stdout, "", TerminationReason.NATURAL_EXIT)
    sr = _build_skill_result(
        proc_result,
        completion_marker="%%ORDER_UP::abc%%",
        expected_output_patterns=[r"worktree_path[ \t]*=[ \t]*/.+"],
        completion_required=True,
        backend=ClaudeCodeBackend(),
    )
    assert sr.success is False


def test_worktree_only_session_with_stdout_write_heuristic_and_completion_required_fails():
    truncated_line = (
        '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Write","id":"t1"'
    )
    result_text = "worktree_path = /tmp/worktrees/impl-foo\nbranch_name = impl/foo"
    result_record = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": result_text,
            "session_id": "s1",
        }
    )
    stdout = truncated_line + "\n" + result_record
    proc_result = _sr(0, stdout, "", TerminationReason.NATURAL_EXIT)
    sr = _build_skill_result(
        proc_result,
        completion_marker="%%ORDER_UP::abc123%%",
        expected_output_patterns=[r"worktree_path[ \t]*=[ \t]*/.+"],
        write_behavior=WriteBehaviorSpec(mode="always"),
        completion_required=True,
        backend=ClaudeCodeBackend(),
    )
    assert sr.success is False


def test_completion_required_false_preserves_existing_behavior():
    result_text = "worktree_path = /tmp/worktrees/impl-foo\nbranch_name = impl/foo"
    stdout = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": result_text,
            "session_id": "s1",
        }
    )
    proc_result = _sr(0, stdout, "", TerminationReason.NATURAL_EXIT)
    sr = _build_skill_result(
        proc_result,
        completion_marker="%%ORDER_UP::abc123%%",
        expected_output_patterns=[r"worktree_path[ \t]*=[ \t]*/.+"],
        completion_required=False,
        backend=ClaudeCodeBackend(),
    )
    assert sr.success is True


@pytest.mark.parametrize(
    ("cleanup_outcome", "lifecycle_decision"),
    [
        (
            CleanupOutcome(succeeded=False, budget_exhausted=True),
            LifecycleDecision.CONTINUE,
        ),
        (None, LifecycleDecision.CATCH_UP_FAILED),
    ],
)
def test_optional_marker_recovery_cannot_override_lifecycle_failure(
    cleanup_outcome: CleanupOutcome | None,
    lifecycle_decision: LifecycleDecision,
) -> None:
    result_text = "worktree_path = /tmp/worktrees/impl-foo\nbranch_name = impl/foo"
    stdout = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": result_text,
            "session_id": "s1",
        }
    )
    proc_result = dataclasses.replace(
        _sr(0, stdout, "", TerminationReason.NATURAL_EXIT),
        cleanup_outcome=cleanup_outcome,
        lifecycle_decision=lifecycle_decision,
    )

    sr = _build_skill_result(
        proc_result,
        completion_marker="%%ORDER_UP::abc123%%",
        expected_output_patterns=[r"worktree_path[ \t]*=[ \t]*/.+"],
        completion_required=False,
        backend=ClaudeCodeBackend(),
    )

    assert sr.success is False
    assert sr.needs_retry is True
    assert sr.retry_reason == RetryReason.RESUME
    assert sr.subtype == "missing_completion_marker"
