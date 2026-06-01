"""Tests for backend-incompatible-skill semantic rule."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from autoskillit.core import Severity, SkillSource
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_skill_info(
    name: str = "investigate",
    backend_requirements: frozenset[str] = frozenset({"claude-code"}),
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        source=SkillSource.BUNDLED_EXTENDED,
        path=Path("/nonexistent/SKILL.md"),
        categories=frozenset(),
        backend_requirements=backend_requirements,
    )


def _make_recipe_with_skill_step(skill_command: str) -> Recipe:
    steps = {
        "run-skill-step": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": skill_command, "cwd": "/tmp"},
        )
    }
    return Recipe(name="test-recipe", description="test", steps=steps)


def _mock_resolver(skill_info: SimpleNamespace | None) -> MagicMock:
    resolver = MagicMock()
    resolver.resolve.return_value = skill_info
    resolver.list_all.return_value = [skill_info] if skill_info else []
    return resolver


class TestBackendIncompatibleSkillRule:
    def test_incompatible_backend_produces_error(self):
        skill_info = _make_skill_info()
        recipe = _make_recipe_with_skill_step("/investigate something")
        resolver = _mock_resolver(skill_info)
        ctx = make_validation_context(
            recipe,
            backend_name="codex",
            skill_resolver=resolver,
            available_skills=frozenset({"investigate"}),
        )
        findings = run_semantic_rules(ctx)
        compat_findings = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat_findings) == 1
        assert compat_findings[0].severity == Severity.ERROR
        assert "investigate" in compat_findings[0].message
        assert "codex" in compat_findings[0].message

    def test_compatible_backend_no_finding(self):
        skill_info = _make_skill_info()
        recipe = _make_recipe_with_skill_step("/investigate something")
        resolver = _mock_resolver(skill_info)
        ctx = make_validation_context(
            recipe,
            backend_name="claude-code",
            skill_resolver=resolver,
            available_skills=frozenset({"investigate"}),
        )
        findings = run_semantic_rules(ctx)
        compat_findings = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat_findings) == 0

    def test_none_backend_skips_gracefully(self):
        skill_info = _make_skill_info()
        recipe = _make_recipe_with_skill_step("/investigate something")
        resolver = _mock_resolver(skill_info)
        ctx = make_validation_context(
            recipe,
            backend_name=None,
            skill_resolver=resolver,
            available_skills=frozenset({"investigate"}),
        )
        findings = run_semantic_rules(ctx)
        compat_findings = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat_findings) == 0

    def test_empty_backend_requirements_no_finding(self):
        skill_info = _make_skill_info(backend_requirements=frozenset())
        recipe = _make_recipe_with_skill_step("/investigate something")
        resolver = _mock_resolver(skill_info)
        ctx = make_validation_context(
            recipe,
            backend_name="codex",
            skill_resolver=resolver,
            available_skills=frozenset({"investigate"}),
        )
        findings = run_semantic_rules(ctx)
        compat_findings = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat_findings) == 0

    def test_no_resolver_skips_gracefully(self):
        recipe = _make_recipe_with_skill_step("/investigate something")
        ctx = make_validation_context(
            recipe,
            backend_name="codex",
            skill_resolver=None,
            available_skills=frozenset({"investigate"}),
        )
        findings = run_semantic_rules(ctx)
        compat_findings = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat_findings) == 0


_RECIPE_WITH_SKILL_STEP_YAML = """\
name: test-compat
description: recipe with run_skill step
autoskillit_version: "0.2.0"
steps:
  run-skill:
    tool: run_skill
    with:
      skill_command: "/investigate something"
      cwd: /tmp
  stop:
    action: stop
    message: done
"""


class TestBackendNameThreadingAPI:
    """Integration tests verifying backend_name reaches semantic rules via API."""

    def test_load_and_validate_threads_backend_name(self, tmp_path, monkeypatch):
        import autoskillit.recipe._api as api_mod
        import autoskillit.recipe._api_cache as cache_mod

        monkeypatch.setattr(cache_mod, "_LOAD_CACHE", cache_mod.LoadCache())

        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test-compat.yaml").write_text(_RECIPE_WITH_SKILL_STEP_YAML)

        skill_info = _make_skill_info()

        from autoskillit.workspace import DefaultSkillResolver

        monkeypatch.setattr(DefaultSkillResolver, "list_all", lambda self: [skill_info])
        monkeypatch.setattr(DefaultSkillResolver, "resolve", lambda self, name: skill_info)

        result = api_mod.load_and_validate("test-compat", tmp_path, backend_name="codex")

        semantic = result.get("semantic", [])
        compat_findings = [f for f in semantic if f.get("rule") == "backend-incompatible-skill"]
        assert len(compat_findings) == 1
        assert compat_findings[0]["severity"] == "error"

    def test_validate_from_path_threads_backend_name(self, tmp_path, monkeypatch):
        from autoskillit.recipe._api_listing import validate_from_path

        recipe_path = tmp_path / "test-compat.yaml"
        recipe_path.write_text(_RECIPE_WITH_SKILL_STEP_YAML)

        skill_info = _make_skill_info()

        from autoskillit.workspace import DefaultSkillResolver

        monkeypatch.setattr(DefaultSkillResolver, "list_all", lambda self: [skill_info])
        monkeypatch.setattr(DefaultSkillResolver, "resolve", lambda self, name: skill_info)

        result = validate_from_path(recipe_path, backend_name="codex")

        semantic = result.get("semantic", [])
        compat_findings = [f for f in semantic if f.get("rule") == "backend-incompatible-skill"]
        assert len(compat_findings) == 1

    def test_cache_key_varies_by_backend_name(self, tmp_path, monkeypatch):
        import autoskillit.recipe._api as api_mod
        import autoskillit.recipe._api_cache as cache_mod

        monkeypatch.setattr(cache_mod, "_LOAD_CACHE", cache_mod.LoadCache())

        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test-compat.yaml").write_text(_RECIPE_WITH_SKILL_STEP_YAML)

        skill_info = _make_skill_info()

        from autoskillit.workspace import DefaultSkillResolver

        monkeypatch.setattr(DefaultSkillResolver, "list_all", lambda self: [skill_info])
        monkeypatch.setattr(DefaultSkillResolver, "resolve", lambda self, name: skill_info)

        api_mod.load_and_validate("test-compat", tmp_path, backend_name=None)
        api_mod.load_and_validate("test-compat", tmp_path, backend_name="codex")

        cache = cache_mod._LOAD_CACHE
        assert len(cache._store) == 2
