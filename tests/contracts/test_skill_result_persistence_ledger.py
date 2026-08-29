"""Closed durability ledger derived from SkillResult's live dataclass leaves.

The leaf vocabulary is reflected from the live types. Only persistence policy is
hand-maintained because durable-versus-live classification is a project decision,
not serializer behavior.
"""

from __future__ import annotations

import json
import re
from dataclasses import fields, is_dataclass
from types import UnionType
from typing import get_args, get_origin, get_type_hints

import pytest

from autoskillit.core import ExecutionIdentity, SkillResult
from tests.execution.conftest import _flush

pytestmark = pytest.mark.medium

SKILL_RESULT_PERSISTENCE: tuple[tuple[str, str, str], ...] = (
    (
        "api_failure.api_error_message_seen",
        "api_error_message_seen",
        "live-only:derived-elsewhere",
    ),
    ("api_failure.error_code", "api_error_code", "live-only:derived-elsewhere"),
    ("api_failure.rate_limit.limit_type", "rate_limit_type", "live-only:derived-elsewhere"),
    (
        "api_failure.rate_limit.resets_at_epoch",
        "rate_limit_resets_at_epoch",
        "live-only:derived-elsewhere",
    ),
    ("api_failure.rate_limit.status", "rate_limit_status", "live-only:derived-elsewhere"),
    ("api_failure.status", "api_error_status", "persisted"),
    ("api_failure.terminal_reason", "api_terminal_reason", "live-only:derived-elsewhere"),
    ("api_retry.count", "api_retry_count", "persisted"),
    ("api_retry.exhausted", "api_retry_exhausted", "persisted"),
    ("api_retry.last_error", "api_retry_last_error", "persisted"),
    ("api_retry.last_status", "api_retry_last_status", "persisted"),
    ("audit.attempt_id.value", "audit_attempt_id", "live-only:derived-elsewhere"),
    ("audit.cycle_path", "audit_cycle_path", "live-only:ephemeral-path"),
    ("audit.status", "audit_status", "live-only:derived-elsewhere"),
    ("audit.verdict", "audit_verdict", "live-only:derived-elsewhere"),
    ("branch_name", "branch_name", "live-only:ephemeral-path"),
    ("cli_subtype", "cli_subtype", "persisted"),
    ("completion_required", "completion_required", "live-only:derived-elsewhere"),
    ("contamination.retry_reason", "contamination_retry_reason", "live-only:derived-elsewhere"),
    ("contamination.subtype", "contamination_subtype", "live-only:derived-elsewhere"),
    ("evidence.file_changes_count", "file_changes_count", "persisted"),
    ("evidence.fs_writes_detected", "fs_writes_detected", "persisted"),
    ("evidence.git_writes_detected", "git_writes_detected", "persisted"),
    ("evidence.write_call_count", "write_call_count", "persisted"),
    ("execution_identity.children", "child_executions", "persisted"),
    ("execution_identity.cli_version", "execution_cli_version", "persisted"),
    ("execution_identity.effective_parent_backend", "effective_parent_backend", "persisted"),
    ("execution_identity.effective_parent_effort", "effective_parent_effort", "persisted"),
    ("execution_identity.effective_parent_model", "effective_parent_model", "persisted"),
    ("execution_identity.override_key_path", "backend_override_key_path", "persisted"),
    ("execution_identity.override_tier", "backend_override_tier", "persisted"),
    ("execution_identity.parent_session_id", "parent_session_id", "persisted"),
    ("execution_identity.requested_parent_backend", "requested_parent_backend", "persisted"),
    ("execution_identity.requested_parent_effort", "requested_parent_effort", "persisted"),
    ("execution_identity.requested_parent_model", "requested_parent_model", "persisted"),
    ("exit_code", "exit_code", "persisted"),
    ("infra.cleanup_incomplete", "infra_cleanup_incomplete", "persisted"),
    ("infra.exit_category", "infra_exit_category", "persisted"),
    ("infra.fault_domain", "infra_fault_domain", "persisted"),
    ("is_error", "is_error", "persisted"),
    ("kill_reason", "kill_reason", "summary-only"),
    ("last_stop_reason", "last_stop_reason", "summary-only"),
    ("lifespan_started", "lifespan_started", "live-only:derived-elsewhere"),
    ("ndjson_drift.unknown_event_count", "ndjson_unknown_event_count", "persisted"),
    ("ndjson_drift.unknown_item_count", "ndjson_unknown_item_count", "persisted"),
    ("needs_retry", "needs_retry", "persisted"),
    ("order_id", "order_id", "index-only"),
    ("outcome_fields", "outcome_fields", "index-only"),
    ("outcome_invariant_violated", "outcome_invariant_violated", "index-only"),
    ("outcome_qualifier", "outcome_qualifier", "index-only"),
    ("provider.fallback_activated", "provider_fallback", "persisted"),
    ("provider.provider_used", "provider_used", "persisted"),
    ("result", "result", "live-only:size"),
    ("retry_reason", "retry_reason", "persisted"),
    ("session_id", "session_id", "persisted"),
    ("stderr", "stderr", "live-only:size"),
    ("subtype", "subtype", "persisted"),
    ("success", "success", "persisted"),
    ("token_usage", "token_usage", "live-only:derived-elsewhere"),
    ("worktree_path", "worktree_path", "live-only:ephemeral-path"),
    ("write_path_warnings", "write_path_warnings", "summary-only"),
)

_LIVE_ONLY = re.compile(r"^live-only:(size|secret|ephemeral-path|derived-elsewhere)$")


def _unwrap(annotation):
    origin = get_origin(annotation)
    if origin in (UnionType,):
        args = tuple(arg for arg in get_args(annotation) if arg is not type(None))
        if len(args) == 1:
            return args[0]
    return annotation


def _live_leaves(cls, prefix: str = "") -> set[str]:
    hints = get_type_hints(cls)
    leaves: set[str] = set()
    for field in fields(cls):
        path = f"{prefix}.{field.name}" if prefix else field.name
        annotation = _unwrap(hints[field.name])
        if isinstance(annotation, type) and is_dataclass(annotation):
            leaves.update(_live_leaves(annotation, path))
        else:
            leaves.add(path)
    return leaves


def test_persistence_ledger_is_closed_unique_and_sorted() -> None:
    paths = [row[0] for row in SKILL_RESULT_PERSISTENCE]
    assert set(paths) == _live_leaves(SkillResult)
    assert len(paths) == len(set(paths))
    assert list(SKILL_RESULT_PERSISTENCE) == sorted(SKILL_RESULT_PERSISTENCE)
    for _, artifact_key, classification in SKILL_RESULT_PERSISTENCE:
        assert classification in {
            "persisted",
            "summary-only",
            "index-only",
        } or _LIVE_ONLY.fullmatch(classification)
        if not classification.startswith("live-only:"):
            assert artifact_key


def test_durable_ledger_rows_exist_in_real_flushed_artifacts(tmp_path) -> None:
    identity = ExecutionIdentity(
        requested_parent_backend="codex",
        effective_parent_backend="codex",
        requested_parent_model="requested",
        effective_parent_model="effective",
        requested_parent_effort="high",
        effective_parent_effort="max",
        cli_version="1",
        override_tier="step",
        override_key_path="recipe.steps.test",
        parent_session_id="parent",
    )
    _flush(
        tmp_path,
        success=False,
        needs_retry=True,
        retry_reason="resume",
        infra_exit_category="api_error",
        infra_cleanup_incomplete=True,
        infra_fault_domain="infrastructure",
        api_error_status=503,
        is_error=True,
        execution_identity=identity,
        outcome_fields={"attempt": 1},
        outcome_invariant_violated=True,
        outcome_qualifier="retry",
    )
    summary = json.loads((tmp_path / "sessions" / "test-session-001" / "summary.json").read_text())
    index = json.loads((tmp_path / "sessions.jsonl").read_text().strip())

    for leaf, artifact_key, classification in SKILL_RESULT_PERSISTENCE:
        if classification in {"persisted", "summary-only"}:
            if leaf.startswith("execution_identity."):
                summary_key = leaf.removeprefix("execution_identity.")
                assert summary_key in summary["execution_identity"]
            else:
                assert artifact_key in summary
        if classification in {"persisted", "index-only"}:
            assert artifact_key in index
