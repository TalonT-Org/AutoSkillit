"""Tests that critical SKILL.md preamble patterns are present.

These encode behavioral contracts derived from friction analysis (issue #250):
- FRICT-1B-1: code-index initialization
- FRICT-1C-2: relative path examples
- FRICT-2-2: pre-implementation checklist
- FRICT-5-1: path-existence verification
- FRICT-5-3: external repo path validation
"""

from autoskillit.workspace.skills import DefaultSkillResolver


def _skill_md(skill_name: str) -> str:
    result = DefaultSkillResolver().resolve(skill_name)
    assert result is not None, f"Skill {skill_name!r} not found in any bundled skills directory"
    return result.path.read_text()


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
