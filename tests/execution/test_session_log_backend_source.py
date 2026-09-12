from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

_BACKEND_AUTHORITY = {
    "backend": "claude-code",
    "kind": "step",
    "tier": "step",
    "key_path": "recipe.steps.implement.backend",
}
_LAUNCH_CONTRACT_DIGEST = "a" * 64


class TestBackendAuthorityInSessionsJsonl:
    def test_launch_authority_fields_present(self) -> None:
        """sessions.jsonl entries include typed launch evidence."""
        from autoskillit.core.types._type_results import (
            SessionIndexEntry,
        )

        annotations = SessionIndexEntry.__annotations__
        assert "backend_authority" in annotations
        assert "launch_contract_digest" in annotations

    def test_session_index_entry_default_value(self) -> None:
        """When no override is used, the field defaults to None."""
        from autoskillit.core.types._type_results import (
            SESSION_INDEX_SCHEMA_VERSION,
            SessionIndexEntry,
        )

        entry: SessionIndexEntry = {  # type: ignore[typeddict-item]
            "session_id": "x",
            "dir_name": "y",
            "timestamp": "",
            "cwd": "",
            "kitchen_id": "",
            "order_id": "",
            "campaign_id": "",
            "dispatch_id": "",
            "claude_code_log": None,
            "codex_log": None,
            "backend": "claude-code",
            "backend_authority": None,
            "launch_contract_digest": "",
            "requested_parent_backend": "",
            "effective_parent_backend": "",
            "requested_parent_model": "",
            "effective_parent_model": "",
            "requested_parent_effort": "",
            "effective_parent_effort": "",
            "execution_cli_version": "",
            "backend_override_tier": "",
            "backend_override_key_path": "",
            "parent_session_id": "",
            "child_executions": [],
            "skill_command": "",
            "success": True,
            "needs_retry": False,
            "retry_reason": "none",
            "infra_exit_category": "completed",
            "infra_cleanup_incomplete": False,
            "infra_fault_domain": "unknown",
            "api_error_status": None,
            "is_error": False,
            "subtype": "",
            "cli_subtype": "",
            "exit_code": 0,
            "snapshot_count": 0,
            "anomaly_count": 0,
            "peak_rss_kb": 0,
            "peak_oom_score": 0,
            "step_name": "",
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_write_tokens": 0,
            "cache_read_tokens": 0,
            "write_call_count": 0,
            "fs_writes_detected": False,
            "git_writes_detected": False,
            "file_changes_count": 0,
            "tracked_comm": None,
            "tracked_comm_drift": False,
            "autoskillit_version": "",
            "claude_code_version": "",
            "codex_version": "",
            "recipe_name": "",
            "recipe_content_hash": "",
            "recipe_composite_hash": "",
            "recipe_version": "",
            "duration_seconds": 0.0,
            "github_api_requests": 0,
            "provider_used": "",
            "provider_fallback": False,
            "model_identifier": "",
            "configured_model": "",
            "profile_name": "",
            "caller_session_id": "",
            "api_retry_count": 0,
            "api_retry_exhausted": False,
            "api_retry_last_error": "",
            "api_retry_last_status": None,
            "ndjson_unknown_event_count": 0,
            "ndjson_unknown_item_count": 0,
            "outcome_fields": None,
            "outcome_invariant_violated": False,
            "outcome_qualifier": None,
            "native_shell_capture": None,
            "session_type": None,
            "subagent_model_outcomes": [],
            "child_outcomes": [],
            "schema_version": SESSION_INDEX_SCHEMA_VERSION,
        }
        assert entry["backend_authority"] is None
        assert entry["launch_contract_digest"] == ""

    def test_typed_backend_authority_in_summary(self, tmp_path):
        import json

        from tests.execution.conftest import _flush

        _flush(
            tmp_path,
            backend_authority=_BACKEND_AUTHORITY,
            launch_contract_digest=_LAUNCH_CONTRACT_DIGEST,
        )
        summary = json.loads(
            (tmp_path / "sessions" / "test-session-001" / "summary.json").read_text()
        )
        assert summary["backend_authority"] == _BACKEND_AUTHORITY
        assert summary["launch_contract_digest"] == _LAUNCH_CONTRACT_DIGEST

    def test_typed_backend_authority_in_sessions_jsonl(self, tmp_path):
        import json

        from tests.execution.conftest import _flush

        _flush(
            tmp_path,
            backend_authority=_BACKEND_AUTHORITY,
            launch_contract_digest=_LAUNCH_CONTRACT_DIGEST,
        )
        lines = (tmp_path / "sessions.jsonl").read_text().strip().split("\n")
        entry = json.loads(lines[-1])
        assert entry["backend_authority"] == _BACKEND_AUTHORITY
        assert entry["launch_contract_digest"] == _LAUNCH_CONTRACT_DIGEST

    def test_launch_evidence_empty_when_unavailable_round_trip(self, tmp_path):
        import json

        from tests.execution.conftest import _flush

        _flush(tmp_path)
        summary = json.loads(
            (tmp_path / "sessions" / "test-session-001" / "summary.json").read_text()
        )
        assert summary["backend_authority"] is None
        assert summary["launch_contract_digest"] == ""
        assert summary["session_type"] is None
        lines = (tmp_path / "sessions.jsonl").read_text().strip().split("\n")
        entry = json.loads(lines[-1])
        assert entry["backend_authority"] is None
        assert entry["launch_contract_digest"] == ""
        assert entry["session_type"] is None


class TestChildOutcomesProjection:
    """child_outcomes (issue #4623) round-trips into summary.json and sessions.jsonl."""

    _ROW_UNKNOWN = {
        "child_id": "c1",
        "launch_alias": "",
        "backend": "claude_code",
        "parent_session_id": "test-session-001",
        "role": "",
        "attribution_skill": "",
        "effective_model": "",
        "effective_effort": "",
        "effective_provider": "",
        "terminal_reason": "unknown",
        "raw_reason": "",
        "raw_subtype": "",
        "raw_code": "",
        "evidence_source": "subagent_start",
        "transcript_locator": "",
        "start_confirmed": True,
    }

    def test_child_outcomes_round_trip(self, tmp_path):
        import json

        from tests.execution.conftest import _flush

        _flush(tmp_path, child_outcomes=[self._ROW_UNKNOWN])
        summary = json.loads(
            (tmp_path / "sessions" / "test-session-001" / "summary.json").read_text()
        )
        assert summary["child_outcomes"] == [self._ROW_UNKNOWN]
        lines = (tmp_path / "sessions.jsonl").read_text().strip().split("\n")
        entry = json.loads(lines[-1])
        assert entry["child_outcomes"] == [self._ROW_UNKNOWN]

    def test_reused_recovery_refreshes_child_outcomes_in_committed_summary_and_index(
        self, tmp_path
    ):
        """A reused-recovery flush (publish_artifacts=False) still refines unknown to
        a known reason in the already-committed summary.json, and the index (which
        rewrites unconditionally) reflects it too."""
        import json

        from tests.execution.conftest import _flush

        _flush(tmp_path, child_outcomes=[self._ROW_UNKNOWN])
        refined_row = {**self._ROW_UNKNOWN, "terminal_reason": "completed"}

        _flush(tmp_path, child_outcomes=[refined_row], is_crash_recovery=True)

        summary = json.loads(
            (tmp_path / "sessions" / "test-session-001" / "summary.json").read_text()
        )
        assert summary["child_outcomes"] == [refined_row]
        lines = (tmp_path / "sessions.jsonl").read_text().strip().split("\n")
        entry = json.loads(lines[-1])
        assert entry["child_outcomes"] == [refined_row]

    def test_reused_recovery_leaves_other_summary_fields_untouched(self, tmp_path):
        """The reuse-recovery refresh patches only child_outcomes, not the rest of
        the already-committed summary (e.g. an unrelated field stays as first-flushed)."""
        import json

        from tests.execution.conftest import _flush

        _flush(tmp_path, child_outcomes=[self._ROW_UNKNOWN], skill_command="/first:command")
        refined_row = {**self._ROW_UNKNOWN, "terminal_reason": "completed"}

        _flush(
            tmp_path,
            child_outcomes=[refined_row],
            is_crash_recovery=True,
            skill_command="/second:command",
        )

        summary = json.loads(
            (tmp_path / "sessions" / "test-session-001" / "summary.json").read_text()
        )
        assert summary["child_outcomes"] == [refined_row]
        assert summary["skill_command"] == "/first:command"
