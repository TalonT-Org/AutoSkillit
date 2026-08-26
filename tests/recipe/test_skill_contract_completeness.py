"""Tests for SKILL.md to skill_contracts.yaml completeness (planner-refine focus)."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import ClaudeDirectoryConventions, SkillExecutionRole, pkg_root
from autoskillit.recipe.contracts import load_bundled_manifest, resolve_skill_name
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.workspace.session_skills import (
    DefaultSessionSkillManager,
    SkillsDirectoryProvider,
)
from autoskillit.workspace.skills import DefaultSkillResolver

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


class TestPlannerRefineContract:
    """planner-refine-specific contract completeness tests for Part A."""

    def test_planner_refine_outputs_in_contract(self) -> None:
        """planner-refine contract must declare issues_fixed and refinement_complete."""
        manifest = load_bundled_manifest()
        contract = manifest.get("skills", {}).get("planner-refine")
        assert contract is not None, "planner-refine not in bundled manifest"
        output_names = {o["name"] for o in contract.get("outputs", [])}
        assert "issues_fixed" in output_names, (
            f"planner-refine contract missing 'issues_fixed'. Found: {output_names}"
        )
        assert "refinement_complete" in output_names, (
            f"planner-refine contract missing 'refinement_complete'. Found: {output_names}"
        )


def test_bundled_recipe_skill_targets_resolve_and_materialize(tmp_path: Path) -> None:
    """Recipe run_skill targets must survive resolver and generated add-dir projection."""
    targets: set[str] = set()
    for recipe_path in sorted(builtin_recipes_dir().glob("*.yaml")):
        recipe = load_recipe(recipe_path)
        for step_name, step in recipe.steps.items():
            if step.tool != "run_skill":
                continue
            skill_command = str(step.with_args.get("skill_command", ""))
            skill_name = resolve_skill_name(skill_command)
            assert skill_name is not None, (
                f"{recipe_path.name}:{step_name} has an unparseable skill command: "
                f"{skill_command!r}"
            )
            targets.add(skill_name)

    assert targets, "Bundled recipes must declare at least one run_skill target"
    resolver = DefaultSkillResolver()
    unresolved = sorted(name for name in targets if resolver.resolve(name) is None)
    assert not unresolved, (
        f"Bundled recipe targets missing from DefaultSkillResolver: {unresolved}"
    )

    provider = SkillsDirectoryProvider()
    catalog = resolver.list_effective(tmp_path, SkillExecutionRole.SESSION)
    projection_context = provider.catalog_projection_context(
        catalog,
        tmp_path,
        durable_scripts_root=pkg_root(),
    )
    manager = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path / "sessions")
    generated_home = manager.init_session(
        "bundled-recipe-targets",
        catalog,
        projection_context,
    )

    skills_dir = Path(generated_home.path) / ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR
    missing = sorted(name for name in targets if not (skills_dir / name / "SKILL.md").is_file())
    assert not missing, (
        "Bundled recipe targets must materialize under the generated add-dir, not a "
        f"repository-root skill cache: {missing}"
    )
