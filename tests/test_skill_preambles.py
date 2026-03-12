"""Tests that critical SKILL.md preamble patterns are present.

These encode behavioral contracts derived from friction analysis (issue #250):
- FRICT-1B-1: code-index initialization
- FRICT-1C-2: relative path examples
- FRICT-2-2: pre-implementation checklist
- FRICT-5-1: path-existence verification
- FRICT-5-3: external repo path validation
"""

import pytest

from autoskillit.core.paths import pkg_root


def _skill_md(skill_name: str) -> str:
    return (pkg_root() / "skills" / skill_name / "SKILL.md").read_text()


CODE_INDEX_SKILLS = [
    "investigate",
    "audit-impl",
    "make-plan",
    "make-groups",
    "rectify",
    "triage-issues",
    "resolve-failures",
]


@pytest.mark.parametrize("skill_name", CODE_INDEX_SKILLS)
def test_code_index_skills_have_set_project_path(skill_name):
    """Each code-index skill must call set_project_path in its workflow preamble."""
    content = _skill_md(skill_name)
    assert "set_project_path" in content, (
        f"{skill_name}/SKILL.md is missing a set_project_path call. "
        "Agents using code-index tools without initialization fail with "
        "'Project path not set' (FRICT-1B-1)."
    )


@pytest.mark.parametrize("skill_name", CODE_INDEX_SKILLS)
def test_code_index_skills_have_relative_path_example(skill_name):
    """Each updated skill must include a project-relative path example."""
    content = _skill_md(skill_name)
    assert "src/" in content, (
        f"{skill_name}/SKILL.md is missing a project-relative path example "
        "(e.g., src/<your_package>/some_module.py). Agents copy absolute "
        "paths from Read output and code-index rejects them (FRICT-1C-2)."
    )


def test_implement_worktree_has_pre_implementation_checklist():
    """implement-worktree SKILL.md must contain the pre-implementation checklist."""
    content = _skill_md("implement-worktree")
    assert "pre-implementation checklist" in content.lower(), (
        "implement-worktree/SKILL.md is missing the pre-implementation checklist. "
        "This prevents test fix cycles from registration mismatches (FRICT-2-2)."
    )


def test_setup_project_has_path_validation():
    """setup-project SKILL.md must include project_dir git-repo validation."""
    content = _skill_md("setup-project")
    # Must mention the explicit git repo check added by FRICT-5-3
    assert "is-inside-work-tree" in content, (
        "setup-project/SKILL.md is missing explicit git-repo validation. "
        "Agents should run `git rev-parse --is-inside-work-tree` before "
        "assuming project_dir is a valid git repo (FRICT-5-3)."
    )
