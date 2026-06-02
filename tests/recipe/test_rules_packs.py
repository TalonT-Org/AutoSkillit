"""Tests for the unknown-required-pack and undeclared-pack-requirement semantic rules."""

from __future__ import annotations

from dataclasses import replace

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(requires_packs: list[str]) -> Recipe:
    return Recipe(
        name="test",
        description="test recipe",
        version="0.7.2",
        requires_packs=requires_packs,
        steps={"stop": RecipeStep(action="stop")},
    )


def _make_recipe_with_run_skill(
    requires_packs: list[str],
    skill_command: str,
    step_name: str = "run_it",
) -> Recipe:
    return Recipe(
        name="test",
        description="test recipe",
        version="0.7.2",
        requires_packs=requires_packs,
        steps={
            step_name: RecipeStep(
                tool="run_skill",
                with_args={"skill_command": skill_command},
            )
        },
    )


# ----------------------------------------------------------------------
# unknown-required-pack tests
# ----------------------------------------------------------------------


def test_unknown_pack_produces_error():
    """Pack name not in PACK_REGISTRY produces an ERROR finding."""
    import autoskillit.recipe  # noqa: F401 -- triggers rule registration

    recipe = _make_recipe(["nonexistent-pack"])
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "unknown-required-pack"]
    assert findings
    assert findings[0].severity == Severity.ERROR
    assert "nonexistent-pack" in findings[0].message


def test_known_pack_produces_no_finding():
    """Known pack name (in PACK_REGISTRY) produces no finding."""
    import autoskillit.recipe  # noqa: F401 -- triggers rule registration

    recipe = _make_recipe(["research"])
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "unknown-required-pack"]
    assert not findings


def test_mixed_packs_flags_only_unknown():
    """Only unknown packs are flagged; known packs pass silently."""
    import autoskillit.recipe  # noqa: F401 -- triggers rule registration

    recipe = _make_recipe(["research", "bogus-pack"])
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "unknown-required-pack"]
    assert len(findings) == 1
    assert "bogus-pack" in findings[0].message


def test_empty_requires_packs_produces_no_finding():
    """Recipes without requires_packs produce no finding."""
    import autoskillit.recipe  # noqa: F401 -- triggers rule registration

    recipe = _make_recipe([])
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "unknown-required-pack"]
    assert not findings


def test_all_builtin_packs_pass():
    """Every pack in PACK_REGISTRY is a valid name (no self-flagging)."""
    import autoskillit.recipe  # noqa: F401 -- triggers rule registration
    from autoskillit.core import PACK_REGISTRY

    recipe = _make_recipe(list(PACK_REGISTRY.keys()))
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "unknown-required-pack"]
    assert not findings, f"Built-in packs must not trigger unknown-required-pack: {findings}"


def test_full_audit_declares_audit_pipeline_pack() -> None:
    from autoskillit.core import pkg_root
    from autoskillit.recipe.io import load_recipe

    recipe = load_recipe(pkg_root() / "recipes" / "full-audit.yaml")
    assert "audit-pipeline" in recipe.requires_packs


# ----------------------------------------------------------------------
# undeclared-pack-requirement tests
# ----------------------------------------------------------------------


class TestUndeclaredPackRequirement:
    """Tests for the undeclared-pack-requirement semantic rule."""

    def _run_rule(
        self,
        recipe: Recipe,
        skill_category_map: dict[str, frozenset[str]] | None = None,
    ) -> list:
        import autoskillit.recipe  # noqa: F401 -- triggers rule registration

        if skill_category_map is not None:
            from autoskillit.recipe._analysis import make_validation_context

            ctx = make_validation_context(recipe)
            ctx.skill_category_map = skill_category_map
            return [f for f in run_semantic_rules(ctx) if f.rule == "undeclared-pack-requirement"]
        return [f for f in run_semantic_rules(recipe) if f.rule == "undeclared-pack-requirement"]

    def test_skill_needing_disabled_pack_without_declaration_is_error(self):
        """A recipe that uses a vis-lens skill without declaring vis-lens emits ERROR."""
        recipe = _make_recipe_with_run_skill(
            requires_packs=["research"],  # missing vis-lens
            skill_command="/autoskillit:plan-visualization",
        )
        mock_map = {"plan-visualization": frozenset({"vis-lens"})}
        findings = self._run_rule(recipe, skill_category_map=mock_map)
        assert len(findings) == 1
        assert findings[0].severity == Severity.ERROR
        assert "vis-lens" in findings[0].message

    def test_skill_needing_disabled_pack_with_declaration_passes(self):
        """A recipe that uses a vis-lens skill AND declares vis-lens emits no finding."""
        recipe = _make_recipe_with_run_skill(
            requires_packs=["research", "vis-lens"],
            skill_command="/autoskillit:plan-visualization",
        )
        mock_map = {"plan-visualization": frozenset({"vis-lens"})}
        findings = self._run_rule(recipe, skill_category_map=mock_map)
        assert not findings

    def test_skill_needing_only_enabled_packs_passes(self):
        """A recipe that uses only default-enabled pack skills emits no finding."""
        from autoskillit.core import PACK_REGISTRY

        assert PACK_REGISTRY["github"].default_enabled, (
            "precondition: github must be default-enabled"
        )
        recipe = _make_recipe_with_run_skill(
            requires_packs=["github"],
            skill_command="/autoskillit:github-issue",
        )
        mock_map = {"github-issue": frozenset({"github"})}
        findings = self._run_rule(recipe, skill_category_map=mock_map)
        assert not findings

    def test_multiple_missing_packs_emits_multiple_errors(self):
        """A recipe missing both exp-lens and vis-lens emits two findings."""
        recipe = _make_recipe_with_run_skill(
            requires_packs=["research"],  # missing both exp-lens and vis-lens
            skill_command="/autoskillit:experiment-compare",
        )
        mock_map = {"experiment-compare": frozenset({"exp-lens", "vis-lens"})}
        findings = self._run_rule(recipe, skill_category_map=mock_map)
        assert len(findings) == 2
        assert all(f.severity == Severity.ERROR for f in findings)
        pack_names = {p for f in findings for p in ["exp-lens", "vis-lens"] if p in f.message}
        assert pack_names == {"exp-lens", "vis-lens"}

    def test_dynamic_skill_name_is_skipped(self):
        """A skill_command using a template expression emits no finding (graceful skip)."""
        recipe = Recipe(
            name="test",
            description="test recipe",
            requires_packs=["research"],
            steps={
                "run_it": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "${{ inputs.target_skill }}"},
                )
            },
        )
        findings = self._run_rule(recipe)
        assert not findings

    def test_unknown_skill_name_is_skipped(self):
        """Non-existent skill reference emits no finding (handled by unknown-skill-command)."""
        recipe = _make_recipe_with_run_skill(
            requires_packs=["research"],
            skill_command="/autoskillit:nonexistent-skill-xyz",
        )
        findings = self._run_rule(recipe)
        assert not findings

    def test_research_design_yaml_triggers_error_without_vis_lens(self):
        """research-design.yaml with only [research] triggers vis-lens ERROR."""
        import autoskillit.recipe  # noqa: F401 -- triggers rule registration

        base_recipe = load_recipe(builtin_recipes_dir() / "research-design.yaml")
        recipe = replace(base_recipe, requires_packs=["research"])
        findings = [
            f for f in run_semantic_rules(recipe) if f.rule == "undeclared-pack-requirement"
        ]
        assert len(findings) >= 1, "Expected vis-lens to be flagged as missing"
        vis_lens_findings = [f for f in findings if "vis-lens" in f.message]
        assert len(vis_lens_findings) == 2
        assert all(f.severity == Severity.ERROR for f in vis_lens_findings)
