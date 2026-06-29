from __future__ import annotations

from datetime import date, timedelta

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


class TestCheckCodexModelAliasStaleness:
    """Tests for the codex_model_alias_staleness doctor check (Check 36)."""

    def test_ok_when_within_threshold_and_valid_aliases(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import autoskillit.cli.doctor._doctor_runtime as mod
        from autoskillit.core import Severity

        recent = (date.today() - timedelta(days=1)).isoformat()
        monkeypatch.setattr(mod, "CODEX_MODEL_ALIASES_LAST_VERIFIED", recent)
        monkeypatch.setattr(
            mod,
            "CODEX_MODEL_ALIASES",
            {"sonnet": "gpt-5.4", "opus": "gpt-5.5", "haiku": "gpt-5.4-mini"},
        )
        result = mod._check_codex_model_alias_staleness()
        assert result.severity == Severity.OK
        assert result.check == "codex_model_alias_staleness"

    def test_warning_when_date_exceeds_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import autoskillit.cli.doctor._doctor_runtime as mod
        from autoskillit.core import Severity

        stale = (date.today() - timedelta(days=91)).isoformat()
        monkeypatch.setattr(mod, "CODEX_MODEL_ALIASES_LAST_VERIFIED", stale)
        result = mod._check_codex_model_alias_staleness()
        assert result.severity == Severity.WARNING
        assert result.check == "codex_model_alias_staleness"
        assert "91" in result.message

    def test_warning_when_alias_value_invalid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import autoskillit.cli.doctor._doctor_runtime as mod
        from autoskillit.core import Severity

        monkeypatch.setattr(
            mod,
            "CODEX_MODEL_ALIASES",
            {"sonnet": "gpt-5.4", "opus": "BOGUS-MODEL"},
        )
        result = mod._check_codex_model_alias_staleness()
        assert result.severity == Severity.WARNING
        assert result.check == "codex_model_alias_staleness"
        assert "opus" in result.message
        assert "BOGUS-MODEL" in result.message

    def test_stale_date_takes_priority_over_invalid_alias(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import autoskillit.cli.doctor._doctor_runtime as mod
        from autoskillit.core import Severity

        stale = (date.today() - timedelta(days=91)).isoformat()
        monkeypatch.setattr(mod, "CODEX_MODEL_ALIASES_LAST_VERIFIED", stale)
        monkeypatch.setattr(mod, "CODEX_MODEL_ALIASES", {"opus": "BOGUS-MODEL"})
        result = mod._check_codex_model_alias_staleness()
        assert result.severity == Severity.WARNING
        assert "91" in result.message
        assert "BOGUS-MODEL" not in result.message

    def test_check_name_is_codex_model_alias_staleness(self) -> None:
        from autoskillit.cli.doctor._doctor_runtime import (
            _check_codex_model_alias_staleness,
        )

        result = _check_codex_model_alias_staleness()
        assert result.check == "codex_model_alias_staleness"
