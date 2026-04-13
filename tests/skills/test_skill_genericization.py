"""Verify skill SKILL.md files contain no project-specific AutoSkillit internals."""

from __future__ import annotations

from pathlib import Path

SKILLS_DIR = Path(__file__).parent.parent.parent / "src/autoskillit/skills"


def _skill_content(name: str) -> str:
    path = SKILLS_DIR / name / "SKILL.md"
    return path.read_text() if path.exists() else ""


def test_implement_worktree_has_no_autoskillit_gate_references() -> None:
    """implement-worktree/SKILL.md must not reference AutoSkillit-specific gate internals."""
    content = _skill_content("implement-worktree")
    forbidden = ["gate.py", "GATED_TOOLS", "UNGATED_TOOLS", "src/autoskillit/pipeline"]
    for term in forbidden:
        assert term not in content, (
            f"implement-worktree/SKILL.md references AutoSkillit-internal '{term}'. "
            "Replace with generic guidance (REQ-GEN-001)."
        )


def test_resolve_failures_uses_config_driven_test_command() -> None:
    """resolve-failures/SKILL.md must not hardcode 'task test-all'."""
    content = _skill_content("resolve-failures")
    assert "task test-all" not in content, (
        "resolve-failures/SKILL.md hardcodes 'task test-all'. "
        "Use config-driven test command reference (REQ-GEN-002)."
    )


def test_implement_worktree_uses_config_driven_test_command() -> None:
    """implement-worktree/SKILL.md must not hardcode 'task test-all'."""
    content = _skill_content("implement-worktree")
    assert "task test-all" not in content, (
        "implement-worktree/SKILL.md hardcodes 'task test-all'. "
        "Use config-driven test command reference (REQ-GEN-002)."
    )


def test_merge_pr_uses_generic_ci_check_name() -> None:
    """merge-pr/SKILL.md must not name project-specific CI checks."""
    content = _skill_content("merge-pr")
    assert "Preflight + Ubuntu" not in content, (
        "merge-pr/SKILL.md hardcodes 'Preflight + Ubuntu' CI check name. "
        "Use generic 'required status checks' language (REQ-GEN-003)."
    )


def test_code_index_examples_are_generic() -> None:
    """No bundled SKILL.md may use src/autoskillit/ as a code-index path example."""
    skills_with_violations: list[str] = []
    for skill_dir in SKILLS_DIR.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        content = skill_md.read_text()
        # Check for the specific AutoSkillit path example in code-index instructions
        if "src/autoskillit/execution/headless.py" in content:
            skills_with_violations.append(skill_dir.name)
    assert not skills_with_violations, (
        f"These skills have AutoSkillit-specific code-index path examples: "
        f"{skills_with_violations}. Replace with generic placeholders (REQ-GEN-004)."
    )


def test_scope_has_no_hardcoded_metrics_rs() -> None:
    """scope/SKILL.md must not reference the hardcoded src/metrics.rs path."""
    skill_dir = Path(__file__).parent.parent.parent / "src/autoskillit/skills_extended"
    content = (skill_dir / "scope" / "SKILL.md").read_text()
    assert "src/metrics.rs" not in content, (
        "scope/SKILL.md hardcodes 'src/metrics.rs'. "
        "Use generic evaluation framework search (REQ-GEN-005)."
    )
    assert "test_metrics_assess" not in content, (
        "scope/SKILL.md hardcodes 'test_metrics_assess'. "
        "Use generic evaluation framework search (REQ-GEN-005)."
    )


def test_plan_experiment_has_no_hardcoded_metrics_rs() -> None:
    """plan-experiment/SKILL.md must not reference the hardcoded src/metrics.rs path."""
    skill_dir = Path(__file__).parent.parent.parent / "src/autoskillit/skills_extended"
    content = (skill_dir / "plan-experiment" / "SKILL.md").read_text()
    assert "src/metrics.rs" not in content, (
        "plan-experiment/SKILL.md hardcodes 'src/metrics.rs'. "
        "Use generic evaluation framework language (REQ-GEN-005)."
    )
