"""Tests for the project-local-skill-override recipe validation rule (T-OVR-015..018)."""

from __future__ import annotations


def test_project_local_override_rule_emits_warning(tmp_path):
    """T-OVR-015: /autoskillit:review-pr with project-local override → WARNING finding."""
    from autoskillit.core import Severity
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    skill_dir = tmp_path / ".claude" / "skills" / "review-pr"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# custom review-pr")
    recipe = Recipe(
        name="test",
        description="test",
        version="0.2.0",
        kitchen_rules="k",
        steps={
            "run": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:review-pr"},
            )
        },
    )
    ctx = make_validation_context(recipe, project_dir=tmp_path)
    findings = run_semantic_rules(ctx)
    warn = [f for f in findings if f.rule == "project-local-skill-override"]
    assert warn, "Expected WARNING finding for project-local override"
    assert all(f.severity == Severity.WARNING for f in warn)
    assert any("review-pr" in f.message for f in warn)


def test_project_local_override_rule_no_finding_without_override(tmp_path):
    """T-OVR-016: No finding when no project-local override exists."""
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    # project_dir exists but has no .claude/skills/review-pr override
    recipe = Recipe(
        name="test",
        description="test",
        version="0.2.0",
        kitchen_rules="k",
        steps={
            "run": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:review-pr"},
            )
        },
    )
    ctx = make_validation_context(recipe, project_dir=tmp_path)
    findings = run_semantic_rules(ctx)
    override_findings = [f for f in findings if f.rule == "project-local-skill-override"]
    assert not override_findings


def test_project_local_override_rule_ignores_bare_command(tmp_path):
    """T-OVR-017: Bare /review-pr command (not /autoskillit:review-pr) → no finding."""
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    # Project-local override exists for "review-pr"
    skill_dir = tmp_path / ".claude" / "skills" / "review-pr"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# custom review-pr")

    # Recipe uses bare /review-pr (not /autoskillit:review-pr)
    recipe = Recipe(
        name="test",
        description="test",
        version="0.2.0",
        kitchen_rules="k",
        steps={
            "run": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/review-pr"},
            )
        },
    )
    ctx = make_validation_context(recipe, project_dir=tmp_path)
    findings = run_semantic_rules(ctx)
    override_findings = [f for f in findings if f.rule == "project-local-skill-override"]
    assert not override_findings


def test_project_local_override_rule_noop_without_project_dir():
    """T-OVR-018: project_dir=None in context → no warnings."""
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    recipe = Recipe(
        name="test",
        description="test",
        version="0.2.0",
        kitchen_rules="k",
        steps={
            "run": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:review-pr"},
            )
        },
    )
    # No project_dir — override detection must be skipped entirely
    ctx = make_validation_context(recipe)
    findings = run_semantic_rules(ctx)
    override_findings = [f for f in findings if f.rule == "project-local-skill-override"]
    assert not override_findings
