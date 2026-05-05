"""Structural guards preventing regression to bare-kwarg provider/recipe omission."""

from __future__ import annotations

import inspect
import json

import pytest

from autoskillit.core.types._type_results import ProviderOutcome, RecipeIdentity

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestFlushSignatureGuard:
    """flush_session_log must not accept bare provider or recipe string kwargs."""

    def test_no_bare_provider_used_kwarg(self):
        from autoskillit.execution.session_log import flush_session_log

        sig = inspect.signature(flush_session_log)
        assert "provider_used" not in sig.parameters

    def test_no_bare_provider_fallback_kwarg(self):
        from autoskillit.execution.session_log import flush_session_log

        sig = inspect.signature(flush_session_log)
        assert "provider_fallback" not in sig.parameters

    def test_no_bare_recipe_name_kwarg(self):
        from autoskillit.execution.session_log import flush_session_log

        sig = inspect.signature(flush_session_log)
        assert "recipe_name" not in sig.parameters

    def test_no_bare_recipe_content_hash_kwarg(self):
        from autoskillit.execution.session_log import flush_session_log

        sig = inspect.signature(flush_session_log)
        assert "recipe_content_hash" not in sig.parameters

    def test_provider_outcome_is_required_parameter(self):
        from autoskillit.execution.session_log import flush_session_log

        sig = inspect.signature(flush_session_log)
        assert "provider_outcome" in sig.parameters
        param = sig.parameters["provider_outcome"]
        assert param.default is inspect.Parameter.empty

    def test_recipe_identity_is_required_parameter(self):
        from autoskillit.execution.session_log import flush_session_log

        sig = inspect.signature(flush_session_log)
        assert "recipe_identity" in sig.parameters
        param = sig.parameters["recipe_identity"]
        assert param.default is inspect.Parameter.empty


class TestFlushOutputCompleteness:
    """Every ProviderOutcome field must appear in flush output."""

    def test_provider_outcome_fields_written_to_summary(self, tmp_path):
        from autoskillit.core.types._type_results import SessionTelemetry
        from autoskillit.execution.session_log import flush_session_log

        outcome = ProviderOutcome(provider_used="test-provider", fallback_activated=True)
        flush_session_log(
            log_dir=str(tmp_path),
            cwd="/tmp",
            session_id="completeness-test-001",
            pid=1,
            skill_command="/test",
            success=True,
            subtype="completed",
            exit_code=0,
            start_ts="2026-05-05T00:00:00+00:00",
            proc_snapshots=None,
            provider_outcome=outcome,
            recipe_identity=RecipeIdentity.empty(),
            telemetry=SessionTelemetry.empty(),
        )
        summary_path = tmp_path / "sessions" / "completeness-test-001" / "summary.json"
        summary = json.loads(summary_path.read_text())
        assert summary["provider_used"] == "test-provider"
        assert summary["provider_fallback"] is True

    def test_recipe_identity_fields_written_to_index(self, tmp_path):
        from autoskillit.core.types._type_results import SessionTelemetry
        from autoskillit.execution.session_log import flush_session_log

        identity = RecipeIdentity(
            name="my-recipe",
            content_hash="abc123",
            composite_hash="def456",
            version="1.0",
        )
        flush_session_log(
            log_dir=str(tmp_path),
            cwd="/tmp",
            session_id="recipe-completeness-001",
            pid=1,
            skill_command="/test",
            success=True,
            subtype="completed",
            exit_code=0,
            start_ts="2026-05-05T00:00:00+00:00",
            proc_snapshots=None,
            provider_outcome=ProviderOutcome.none_used(),
            recipe_identity=identity,
            telemetry=SessionTelemetry.empty(),
        )
        index_path = tmp_path / "sessions.jsonl"
        entry = json.loads(index_path.read_text().strip())
        assert entry["recipe_name"] == "my-recipe"
        assert entry["recipe_content_hash"] == "abc123"
        assert entry["recipe_composite_hash"] == "def456"
        assert entry["recipe_version"] == "1.0"
