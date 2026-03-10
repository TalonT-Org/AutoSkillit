"""Guards for the gh-pr-merge CI gate introduced in Part B of issue #289."""

from __future__ import annotations

from autoskillit.core.paths import pkg_root

SKILL_PATH = pkg_root() / "skills" / "merge-pr" / "SKILL.md"


def test_merge_pr_skill_references_gh_pr_merge() -> None:
    """merge-pr SKILL.md must reference 'gh pr merge' for the simple merge path."""
    content = SKILL_PATH.read_text()
    assert "gh pr merge" in content, (
        "merge-pr SKILL.md must document using 'gh pr merge' for the simple PR path — "
        "local git merge bypasses GitHub's required status checks"
    )


def test_merge_pr_skill_references_squash_auto() -> None:
    """merge-pr SKILL.md must reference '--squash --auto' for queued auto-merge."""
    content = SKILL_PATH.read_text()
    assert "--squash --auto" in content, (
        "merge-pr SKILL.md must document '--squash --auto' for queued auto-merge — "
        "'--squash' alone cannot detect a regression that drops '--auto'"
    )
