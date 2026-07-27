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
            {
                "sonnet": "gpt-5.6-sol",
                "opus": "gpt-5.6-sol",
                "haiku": "gpt-5.6-sol",
            },
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
            {"sonnet": "gpt-5.6-sol", "opus": "BOGUS-MODEL"},
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


class TestCheckCodexLimitsVerified:
    """Tests for the codex_limits_verified doctor check (Check 39)."""

    def test_codex_limits_verified_warns_when_cli_newer_than_pin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import autoskillit.cli.doctor._doctor_runtime as mod
        from autoskillit.core import Severity
        from autoskillit.execution.backends.codex import CodexBackend

        monkeypatch.setattr(
            mod,
            "_parse_codex_version",
            lambda *, backend=None: mod.CodexVersionResult(parsed=(0, 146, 0), skip_reason=None),
        )
        result = mod._check_codex_limits_verified(backend=CodexBackend())
        assert result.severity == Severity.WARNING
        assert "CODEX_HISTORY_RETENTION_TOKEN_LIMIT" in result.message
        assert "CODEX_AUTO_COMPACT_LIMIT" in result.message

    def test_codex_limits_verified_skips_for_a_backend_without_a_limits_pin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """F3 regression test: a non-Codex backend must never be version-compared
        against the Codex pin, even when it exposes its own version_check_command."""
        import autoskillit.cli.doctor._doctor_runtime as mod
        from autoskillit.core import Severity
        from autoskillit.execution.backends.claude import ClaudeCodeBackend

        monkeypatch.setattr(
            mod,
            "_parse_codex_version",
            lambda *, backend=None: mod.CodexVersionResult(parsed=(2, 1, 197), skip_reason=None),
        )
        result = mod._check_codex_limits_verified(backend=ClaudeCodeBackend())
        assert result.severity == Severity.OK
        assert "Skipped" in result.message

    def test_codex_limits_verified_skips_when_no_backend_is_resolved(self) -> None:
        import autoskillit.cli.doctor._doctor_runtime as mod
        from autoskillit.core import Severity

        result = mod._check_codex_limits_verified(backend=None)
        assert result.severity == Severity.OK
        assert "Skipped" in result.message

    def test_codex_limits_verified_warning_names_every_governed_symbol(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import autoskillit.cli.doctor._doctor_runtime as mod
        from autoskillit.core import Severity
        from autoskillit.execution.backends._codex_config import (
            CODEX_LIMIT_VERIFICATION_REGISTRY,
        )
        from autoskillit.execution.backends.codex import CodexBackend

        monkeypatch.setattr(
            mod,
            "_parse_codex_version",
            lambda *, backend=None: mod.CodexVersionResult(parsed=(0, 146, 0), skip_reason=None),
        )
        result = mod._check_codex_limits_verified(backend=CodexBackend())
        assert result.severity == Severity.WARNING
        for key in CODEX_LIMIT_VERIFICATION_REGISTRY:
            assert key in result.message

    def test_codex_limits_verified_warning_does_not_claim_later_history_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import autoskillit.cli.doctor._doctor_runtime as mod
        from autoskillit.execution.backends.codex import CodexBackend

        monkeypatch.setattr(
            mod,
            "_parse_codex_version",
            lambda *, backend=None: mod.CodexVersionResult(parsed=(0, 146, 0), skip_reason=None),
        )
        result = mod._check_codex_limits_verified(backend=CodexBackend())
        assert "later history only" not in result.message

    def test_codex_limits_verified_ok_at_pinned_version(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import autoskillit.cli.doctor._doctor_runtime as mod
        from autoskillit.core import Severity
        from autoskillit.execution.backends.codex import CodexBackend

        monkeypatch.setattr(
            mod,
            "_parse_codex_version",
            lambda *, backend=None: mod.CodexVersionResult(parsed=(0, 144, 1), skip_reason=None),
        )
        result = mod._check_codex_limits_verified(backend=CodexBackend())
        assert result.severity == Severity.OK

    def test_codex_limits_verified_ok_between_min_and_pin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import autoskillit.cli.doctor._doctor_runtime as mod
        from autoskillit.core import Severity
        from autoskillit.execution.backends.codex import CodexBackend

        monkeypatch.setattr(
            mod,
            "_parse_codex_version",
            lambda *, backend=None: mod.CodexVersionResult(parsed=(0, 135, 0), skip_reason=None),
        )
        result = mod._check_codex_limits_verified(backend=CodexBackend())
        assert result.severity == Severity.OK

    def test_codex_limits_verified_skips_when_codex_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import autoskillit.cli.doctor._doctor_runtime as mod
        from autoskillit.core import Severity
        from autoskillit.execution.backends.codex import CodexBackend

        monkeypatch.setattr(
            mod,
            "_parse_codex_version",
            lambda *, backend=None: mod.CodexVersionResult(
                parsed=None, skip_reason="codex unavailable (FileNotFoundError)"
            ),
        )
        result = mod._check_codex_limits_verified(backend=CodexBackend())
        assert result.severity == Severity.OK
        assert "Skipped" in result.message
