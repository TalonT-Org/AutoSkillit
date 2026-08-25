"""Tests for SkillResult dataclass + Outcome / Provider / Infra bundles."""

from __future__ import annotations

import dataclasses
import json
from typing import Any, ClassVar

import pytest

from autoskillit.core.types import (
    InfraOutcome,
    ProviderOutcome,
    RetryReason,
    SessionOutcome,
    SkillResult,
    WriteEvidence,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_skill_result_cancelled_factory():
    """SkillResult.cancelled() produces a correctly shaped retriable result."""
    from autoskillit.core.types import KillReason

    result = SkillResult.cancelled(skill_command="/test-skill", order_id="oid-123")
    assert result.success is False
    assert result.subtype == "cancelled"
    assert result.needs_retry is True
    assert result.retry_reason == RetryReason.CANCELLED
    assert result.kill_reason == KillReason.EXCEPTION
    assert result.is_error is True
    assert result.exit_code == -1
    assert result.order_id == "oid-123"
    assert "/test-skill" in result.result


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


def test_skill_result_to_json_preserves_nested_execution_identity() -> None:
    from autoskillit.core import ChildExecutionIdentity, ExecutionIdentity

    child = ChildExecutionIdentity(
        "task",
        "semantic-code-navigator",
        "plan",
        "definition",
        requested_backend="codex",
        effective_backend="codex",
        requested_model="gpt-5.6-luna",
        effective_model="gpt-5.6-luna",
        requested_effort="max",
        effective_effort="max",
        session_id="child-session",
    )
    identity = ExecutionIdentity(
        requested_parent_backend="codex",
        effective_parent_backend="codex",
        parent_session_id="parent-session",
        children=(child,),
    )
    result = SkillResult(
        success=True,
        result="Done.",
        session_id="parent-session",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
        execution_identity=identity,
    )

    assert json.loads(result.to_json())["execution_identity"] == identity.to_dict()


class TestSkillResultCrashedFactory:
    def test_crashed_returns_skill_result_with_correct_fields(self) -> None:
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


class TestSkillResultInfeasibleFactory:
    def test_infeasible_returns_terminal_result_with_correct_fields(self) -> None:
        from autoskillit.core.types import KillReason

        result = SkillResult.infeasible(
            skill_name="audit-tests",
            backend="codex",
            diagnostic="fixed-set fan-in is unavailable",
            skill_command="$audit-tests",
            session_id="session-123",
            order_id="order-456",
        )

        assert result.success is False
        assert result.is_error is True
        assert result.subtype == "infeasible"
        assert result.needs_retry is False
        assert result.retry_reason is RetryReason.NONE
        assert result.kill_reason is KillReason.NOT_APPLICABLE
        assert result.exit_code == -1
        assert result.stderr == ""
        assert result.evidence == WriteEvidence.none_observed()
        assert result.outcome is SessionOutcome.FAILED
        assert result.session_id == "session-123"
        assert result.order_id == "order-456"
        assert result.result == (
            "Skill 'audit-tests' is not feasible on backend 'codex': "
            "fixed-set fan-in is unavailable | skill_command='$audit-tests'"
        )


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
        from dataclasses import FrozenInstanceError

        with pytest.raises(FrozenInstanceError):
            outcome.exit_category = "api_error"  # type: ignore[misc]


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

    def test_infra_outcome_surfaces_cleanup_incomplete_flag(self):
        sr = SkillResult(
            **self._BASE_KWARGS,
            infra=InfraOutcome(cleanup_incomplete=True),
        )
        data = json.loads(sr.to_json())
        assert data["infra_cleanup_incomplete"] is True

    def test_infra_outcome_cleanup_incomplete_absent_by_default(self):
        sr = SkillResult(**self._BASE_KWARGS)
        data = json.loads(sr.to_json())
        assert data["infra_cleanup_incomplete"] is False

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


def test_skill_result_infrastructure_fault_factory() -> None:
    """SkillResult.infrastructure_fault() produces a non-retriable fault result

    distinguished on the wire (via the serialized "infra_fault_domain" key,
    not merely the dataclass field) from a logic crash produced by crashed().
    """
    from autoskillit.core import FaultDomain

    result = SkillResult.infrastructure_fault(
        Exception("boom"),
        skill_command="/some-skill",
        session_id="sid",
        order_id="oid",
    )
    assert result.success is False
    assert result.subtype == "infrastructure_fault"
    assert result.infra.fault_domain is FaultDomain.INFRASTRUCTURE
    assert result.needs_retry is False

    data = json.loads(result.to_json())
    assert data["infra_fault_domain"] == "infrastructure"

    crashed_data = json.loads(SkillResult.crashed(Exception("x")).to_json())
    assert crashed_data["infra_fault_domain"] == "logic"
