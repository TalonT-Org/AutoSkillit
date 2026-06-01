"""Tests for backend-incompatible-skill semantic rule."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from autoskillit.core import Severity, SkillSource
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep
from autoskillit.workspace.skills import SkillInfo

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe_with_skill_step(skill_command: str) -> Recipe:
    steps = {
        "run-skill-step": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": skill_command, "cwd": "/tmp"},
        )
    }
    return Recipe(name="test-recipe", steps=steps)


def _mock_resolver(skill_info: SkillInfo | None) -> MagicMock:
    resolver = MagicMock()
    resolver.resolve.return_value = skill_info
    resolver.list_all.return_value = [skill_info] if skill_info else []
    return resolver


class TestBackendIncompatibleSkillRule:
    def test_incompatible_backend_produces_error(self):
        skill_info = SkillInfo(
            name="investigate",
            source=SkillSource.BUNDLED_EXTENDED,
            path=MagicMock(),
            backend_requirements=frozenset({"claude-code"}),
        )
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
        skill_info = SkillInfo(
            name="investigate",
            source=SkillSource.BUNDLED_EXTENDED,
            path=MagicMock(),
            backend_requirements=frozenset({"claude-code"}),
        )
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
        skill_info = SkillInfo(
            name="investigate",
            source=SkillSource.BUNDLED_EXTENDED,
            path=MagicMock(),
            backend_requirements=frozenset({"claude-code"}),
        )
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
        skill_info = SkillInfo(
            name="investigate",
            source=SkillSource.BUNDLED_EXTENDED,
            path=MagicMock(),
            backend_requirements=frozenset(),
        )
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
