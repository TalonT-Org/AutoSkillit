"""Guards for retry-worktree SKILL.md: blind git add prevention."""

from __future__ import annotations

from autoskillit.core.paths import pkg_root

SKILL_MD = pkg_root() / "skills_extended" / "retry-worktree" / "SKILL.md"


def test_retry_worktree_no_blind_add() -> None:
    """retry-worktree/SKILL.md must not contain any blind git add form."""
    text = SKILL_MD.read_text()
    assert "add -A" not in text, "retry-worktree/SKILL.md still contains 'add -A'"
    assert "add --all" not in text, "retry-worktree/SKILL.md still contains 'add --all'"
    for line in text.splitlines():
        stripped = line.strip()
        if "git add ." in stripped and "add -- " not in stripped:
            raise AssertionError(
                f"retry-worktree/SKILL.md contains blind 'git add .': {stripped!r}"
            )
