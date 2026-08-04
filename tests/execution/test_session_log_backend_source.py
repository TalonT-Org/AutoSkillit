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
        from autoskillit.core.types._type_results import SessionIndexEntry

        annotations = SessionIndexEntry.__annotations__
        assert "backend_authority" in annotations
        assert "launch_contract_digest" in annotations

    def test_session_index_entry_default_value(self) -> None:
        """When no override is used, the field defaults to None."""
        from autoskillit.core.types._type_results import SessionIndexEntry

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
            "skill_command": "",
            "success": True,
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
            "schema_version": 7,
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
        lines = (tmp_path / "sessions.jsonl").read_text().strip().split("\n")
        entry = json.loads(lines[-1])
        assert entry["backend_authority"] is None
        assert entry["launch_contract_digest"] == ""
