"""Tests for _check_local_recipe_validity doctor check."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


class TestLocalRecipeValidity:
    def test_broken_local_recipe_reports_error(self, tmp_path: Path) -> None:
        from autoskillit.cli.doctor._doctor_config import _check_local_recipe_validity
        from autoskillit.core import Severity

        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "broken.yaml").write_text(
            "name: broken\n"
            "description: intentionally broken recipe\n"
            'autoskillit_version: "0.2.0"\n'
            "steps:\n"
            "  step_a:\n"
            "    tool: run_skill\n"
            "    on_success: nonexistent_step\n",
            encoding="utf-8",
        )
        results = _check_local_recipe_validity(project_dir=tmp_path)

        errors = [r for r in results if r.severity == Severity.ERROR]
        assert errors, f"Expected ERROR for broken recipe, got: {results}"
        assert "broken.yaml" in errors[0].message

    def test_no_local_recipes_dir_reports_ok(self, tmp_path: Path) -> None:
        from autoskillit.cli.doctor._doctor_config import _check_local_recipe_validity
        from autoskillit.core import Severity

        results = _check_local_recipe_validity(project_dir=tmp_path)

        assert len(results) == 1
        assert results[0].severity == Severity.OK

    def test_empty_recipes_dir_reports_ok(self, tmp_path: Path) -> None:
        from autoskillit.cli.doctor._doctor_config import _check_local_recipe_validity
        from autoskillit.core import Severity

        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        results = _check_local_recipe_validity(project_dir=tmp_path)

        assert len(results) == 1
        assert results[0].severity == Severity.OK
