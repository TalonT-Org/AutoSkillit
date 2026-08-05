"""Tests for _check_standing_backend_pins_feasibility doctor check."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _write_config(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestStandingBackendPinsFeasibility:
    def test_infeasible_pin_reports_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli.doctor._doctor_config import (
            _check_standing_backend_pins_feasibility,
        )
        from autoskillit.core import (
            BackendCapabilities,
            Severity,
            SkillSemanticAdaptationResult,
            SkillSemanticOperation,
        )

        backend = type(
            "UnsupportedBackend",
            (),
            {
                "adapt_skill_semantics": lambda self, plan: (
                    SkillSemanticAdaptationResult.unsupported(
                        backend="codex",
                        operation=SkillSemanticOperation.GIT_METADATA_WRITE,
                    )
                ),
                "capabilities": BackendCapabilities(),
            },
        )()
        monkeypatch.setattr("autoskillit.execution.get_backend", lambda _name: backend)

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        config_dir = tmp_path / "home" / ".autoskillit"
        _write_config(
            config_dir / "config.yaml",
            "agent_backend:\n  recipe_overrides:\n    remediation:\n      assess: codex\n",
        )

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        results = _check_standing_backend_pins_feasibility(project_dir=project_dir)

        errors = [r for r in results if r.severity == Severity.ERROR]
        assert errors, f"Expected at least one ERROR result, got: {results}"
        msg = errors[0].message
        assert "assess" in msg and "codex" in msg

    def test_feasible_pin_reports_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli.doctor._doctor_config import (
            _check_standing_backend_pins_feasibility,
        )
        from autoskillit.core import Severity

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        results = _check_standing_backend_pins_feasibility(project_dir=project_dir)

        assert all(r.severity == Severity.OK for r in results)

    def test_project_override_controls_pinned_backend_feasibility(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli.doctor._doctor_config import (
            _check_standing_backend_pins_feasibility,
        )
        from autoskillit.core import Severity

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        config_dir = tmp_path / "home" / ".autoskillit"
        _write_config(
            config_dir / "config.yaml",
            "agent_backend:\n  recipe_overrides:\n    remediation:\n      assess: codex\n",
        )
        project_dir = tmp_path / "project"
        override = project_dir / ".claude" / "skills" / "resolve-failures" / "SKILL.md"
        override.parent.mkdir(parents=True)
        override.write_text(
            "---\n"
            "name: resolve-failures\n"
            "description: Project-local capability contract.\n"
            "---\n"
            "Resolve the supplied test failures.\n"
        )

        results = _check_standing_backend_pins_feasibility(project_dir=project_dir)

        assert len(results) == 1
        result = results[0]
        assert result.check == "standing_backend_pins_feasibility"
        assert result.severity is Severity.OK

    def test_unknown_backend_degrades_to_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli.doctor._doctor_config import (
            _check_standing_backend_pins_feasibility,
        )
        from autoskillit.core import Severity

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        config_dir = tmp_path / "home" / ".autoskillit"
        _write_config(
            config_dir / "config.yaml",
            "agent_backend:\n"
            "  recipe_overrides:\n"
            "    remediation:\n"
            "      resolve_review: nonexistent_backend_xyz\n",
        )

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        results = _check_standing_backend_pins_feasibility(project_dir=project_dir)

        warnings = [r for r in results if r.severity == Severity.WARNING]
        assert warnings, f"Expected WARNING for unknown backend, got: {results}"

    def _malformed_persistent_backend(self):
        """Stub whose capabilities declare session_dir_persistent but whose
        conventions have no persistent_session_root_subdir (#4391)."""
        from autoskillit.core import BackendCapabilities, BackendConventions

        return type(
            "MalformedPersistentBackend",
            (),
            {
                "capabilities": BackendCapabilities(session_dir_persistent=True),
                "conventions": BackendConventions(),
            },
        )()

    def test_persistent_root_axis_reports_error_for_malformed_convention(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T7 — a recipe pin to a persistent backend with no derivable root
        reports an ERROR naming the dotted config key and the backend name."""
        from autoskillit.cli.doctor._doctor_config import (
            _check_standing_backend_pins_feasibility,
        )
        from autoskillit.core import Severity

        backend = self._malformed_persistent_backend()
        monkeypatch.setattr("autoskillit.execution.get_backend", lambda _name: backend)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        config_dir = tmp_path / "home" / ".autoskillit"
        _write_config(
            config_dir / "config.yaml",
            "agent_backend:\n  recipe_overrides:\n    remediation:\n      assess: codex\n",
        )

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        results = _check_standing_backend_pins_feasibility(project_dir=project_dir)

        errors = [r for r in results if r.severity == Severity.ERROR]
        assert errors, f"Expected at least one ERROR result, got: {results}"
        msg = errors[0].message
        assert "agent_backend.recipe_overrides.remediation.assess" in msg
        assert "codex" in msg

    def test_persistent_root_axis_passes_for_real_codex_backend(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T7 — a well-formed persistent pin (real codex) reports no
        persistent-root ERROR; existing feasible-pin expectations unchanged."""
        from autoskillit.cli.doctor._doctor_config import (
            _check_standing_backend_pins_feasibility,
        )
        from autoskillit.core import Severity

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        config_dir = tmp_path / "home" / ".autoskillit"
        _write_config(
            config_dir / "config.yaml",
            "agent_backend:\n  recipe_overrides:\n    remediation:\n      assess: codex\n",
        )

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        results = _check_standing_backend_pins_feasibility(project_dir=project_dir)

        persistent_root_errors = [
            r
            for r in results
            if r.severity == Severity.ERROR and "persistent session root" in r.message
        ]
        assert not persistent_root_errors, results

    def test_persistent_root_axis_covers_global_step_overrides_pin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T7 — a global step_overrides pin to a malformed persistent backend
        also reports the ERROR, proving the new branch runs before the
        is_recipe_pin early-continue."""
        from autoskillit.cli.doctor._doctor_config import (
            _check_standing_backend_pins_feasibility,
        )
        from autoskillit.core import Severity

        backend = self._malformed_persistent_backend()
        monkeypatch.setattr("autoskillit.execution.get_backend", lambda _name: backend)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        config_dir = tmp_path / "home" / ".autoskillit"
        _write_config(
            config_dir / "config.yaml",
            "agent_backend:\n  step_overrides:\n    resolve_review: codex\n",
        )

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        results = _check_standing_backend_pins_feasibility(project_dir=project_dir)

        errors = [r for r in results if r.severity == Severity.ERROR]
        assert errors, f"Expected at least one ERROR result, got: {results}"
        msg = errors[0].message
        assert "agent_backend.step_overrides.resolve_review" in msg
        assert "codex" in msg
