"""Structural guards: decomposed research-PR skills must not invoke sub-skills
via the Skill tool (that's the root cause of the 100% failure rate)."""

from __future__ import annotations

import pytest

from autoskillit.core.paths import pkg_root

_SKILLS_ROOT = pkg_root() / "skills_extended"


def _read_skill(name: str) -> str:
    path = _SKILLS_ROOT / name / "SKILL.md"
    assert path.exists(), f"SKILL.md not found for {name!r}"
    return path.read_text()


def test_prepare_research_pr_has_no_skill_tool_sub_invocations():
    """prepare-research-pr must never invoke sub-skills via the Skill tool."""
    content = _read_skill("prepare-research-pr")
    # The skill should NOT instruct the model to use the Skill tool for lens skills.
    # It uses Agent subagents for analysis, NOT Skill tool for exp-lens.
    assert "Skill tool" not in content, (
        "prepare-research-pr must never reference the Skill tool — "
        "this recreates the end_turn termination bug"
    )


def test_compose_research_pr_has_no_skill_tool_sub_invocations():
    """compose-research-pr must never invoke sub-skills via the Skill tool."""
    content = _read_skill("compose-research-pr")
    assert "Skill tool" not in content or "exp-lens" not in content


def test_prepare_research_pr_skill_exists():
    """prepare-research-pr SKILL.md must exist after decomposition."""
    path = _SKILLS_ROOT / "prepare-research-pr" / "SKILL.md"
    assert path.exists()


def test_compose_research_pr_skill_exists():
    """compose-research-pr SKILL.md must exist after decomposition."""
    path = _SKILLS_ROOT / "compose-research-pr" / "SKILL.md"
    assert path.exists()


def test_open_research_pr_skill_retired():
    """open-research-pr must be removed — it has been retired."""
    path = _SKILLS_ROOT / "open-research-pr" / "SKILL.md"
    assert not path.exists(), (
        "open-research-pr/SKILL.md still exists — remove it once new skills are in place"
    )


@pytest.mark.parametrize(
    "slug",
    [
        "fair-comparison",
        "estimand-clarity",
        "causal-assumptions",
        "comparator-construction",
        "pipeline-integrity",
        "variance-stability",
        "reproducibility-artifacts",
        "measurement-validity",
        "sensitivity-robustness",
        "benchmark-representativeness",
        "unit-interference",
        "error-budget",
        "severity-testing",
        "randomization-blocking",
        "validity-threats",
        "iterative-learning",
        "exploratory-confirmatory",
        "governance-risk",
    ],
)
def test_exp_lens_skill_has_arguments_section(slug: str):
    """Every exp-lens SKILL.md must have an ## Arguments section."""
    content = _read_skill(f"exp-lens-{slug}")
    assert "## Arguments" in content, (
        f"exp-lens-{slug}/SKILL.md is missing ## Arguments section — "
        "run_experiment_lenses passes context_path and experiment_plan_path positionally"
    )
