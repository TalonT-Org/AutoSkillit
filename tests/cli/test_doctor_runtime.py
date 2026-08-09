from __future__ import annotations

import json
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest

from autoskillit.core import Severity

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


class TestCheckSessionIndexProjection:
    @staticmethod
    def _write_summary(log_root: Path, dir_name: str) -> None:
        session_dir = log_root / "sessions" / dir_name
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "summary.json").write_text(json.dumps({"dir_name": dir_name}))

    @staticmethod
    def _write_index(log_root: Path, *dir_names: str) -> None:
        (log_root / "sessions.jsonl").write_text(
            "".join(
                json.dumps({"dir_name": name, "timestamp": "2000-01-01"}) + "\n"
                for name in dir_names
            )
        )

    @pytest.mark.parametrize(
        ("summaries", "rows", "severity"),
        [
            ((), (), Severity.OK),
            (("complete",), ("complete",), Severity.OK),
            (("missing",), (), Severity.WARNING),
            (("duplicate",), ("duplicate", "duplicate"), Severity.WARNING),
            ((), ("dangling",), Severity.WARNING),
        ],
    )
    def test_projection_multiplicity(
        self,
        tmp_path: Path,
        summaries: tuple[str, ...],
        rows: tuple[str, ...],
        severity: Severity,
    ) -> None:
        from autoskillit.cli.doctor._doctor_runtime import _check_session_index_projection

        for dir_name in summaries:
            self._write_summary(tmp_path, dir_name)
        if rows:
            self._write_index(tmp_path, *rows)

        result = _check_session_index_projection(log_dir=str(tmp_path))

        assert result.severity is severity
        assert not (tmp_path / ".locks" / "sessions-index.lock").exists()

    def test_waits_for_existing_writer_lease(self, tmp_path: Path) -> None:
        from autoskillit.cli.doctor._doctor_runtime import _check_session_index_projection
        from autoskillit.core import ArtifactLease
        from autoskillit.execution.session_log import _session_index_lock_path

        self._write_summary(tmp_path, "complete")
        self._write_index(tmp_path, "complete")
        lock_path = _session_index_lock_path(tmp_path)
        writer = ArtifactLease.acquire_exclusive(lock_path, blocking=True)
        completed = threading.Event()
        result: list[object] = []

        def check() -> None:
            result.append(_check_session_index_projection(log_dir=str(tmp_path)))
            completed.set()

        thread = threading.Thread(target=check)
        try:
            thread.start()
            assert not completed.wait(timeout=0.2)
        finally:
            writer.close()
            thread.join(timeout=5)

        assert completed.is_set()
        assert result[0].severity is Severity.OK

    def test_absent_lock_race_retries_under_existing_shared_lease(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import autoskillit.cli.doctor._doctor_runtime as doctor_runtime
        from autoskillit.core import ArtifactLease
        from autoskillit.execution.session_log import _session_index_lock_path

        self._write_summary(tmp_path, "complete")
        self._write_index(tmp_path, "complete")
        original_read = doctor_runtime._read_session_index_projection
        reads = 0

        def racing_read(log_root: Path):
            nonlocal reads
            reads += 1
            snapshot = original_read(log_root)
            if reads == 1:
                with ArtifactLease.acquire_exclusive(
                    _session_index_lock_path(log_root), blocking=True
                ):
                    pass
            return snapshot

        monkeypatch.setattr(doctor_runtime, "_read_session_index_projection", racing_read)

        result = doctor_runtime._check_session_index_projection(log_dir=str(tmp_path))

        assert result.severity is Severity.OK
        assert reads == 2
