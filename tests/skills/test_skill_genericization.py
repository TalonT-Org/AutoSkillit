"""Verify skill SKILL.md files contain no project-specific AutoSkillit internals."""

from __future__ import annotations

from pathlib import Path

SKILLS_DIR = Path(__file__).parent.parent.parent / "src/autoskillit/skills"
SKILLS_EXTENDED_DIR = Path(__file__).parent.parent.parent / "src/autoskillit/skills_extended"


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


def test_implement_worktree_sets_filter_env() -> None:
    """implement-worktree/SKILL.md must set filter env vars in Step 5."""
    content = (SKILLS_EXTENDED_DIR / "implement-worktree" / "SKILL.md").read_text()
    assert "AUTOSKILLIT_TEST_FILTER" in content, (
        "implement-worktree/SKILL.md must set AUTOSKILLIT_TEST_FILTER before test command"
    )
    assert "AUTOSKILLIT_TEST_BASE_REF" in content, (
        "implement-worktree/SKILL.md must set AUTOSKILLIT_TEST_BASE_REF before test command"
    )


def test_merge_pr_uses_generic_ci_check_name() -> None:
    """merge-pr/SKILL.md must not name project-specific CI checks."""
    content = _skill_content("merge-pr")
    assert "Preflight + Ubuntu" not in content, (
        "merge-pr/SKILL.md hardcodes 'Preflight + Ubuntu' CI check name. "
        "Use generic 'required status checks' language (REQ-GEN-003)."
    )


def test_scope_has_no_hardcoded_metrics_rs() -> None:
    """scope/SKILL.md must not reference the hardcoded src/metrics.rs path."""
    content = (SKILLS_EXTENDED_DIR / "scope" / "SKILL.md").read_text()
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
    content = (SKILLS_EXTENDED_DIR / "plan-experiment" / "SKILL.md").read_text()
    assert "src/metrics.rs" not in content, (
        "plan-experiment/SKILL.md hardcodes 'src/metrics.rs'. "
        "Use generic evaluation framework language (REQ-GEN-005)."
    )
