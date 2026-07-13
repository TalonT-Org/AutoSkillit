from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestBackendOverrideSourceInSessionsJsonl:
    def test_backend_override_source_field_present(self) -> None:
        """sessions.jsonl entries must include the backend_override_source field."""
        from autoskillit.core.types._type_results import SessionIndexEntry

        annotations = SessionIndexEntry.__annotations__
        assert "backend_override_source" in annotations

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
            "backend_override_source": None,
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
            "schema_version": 4,
        }
        assert entry["backend_override_source"] is None
