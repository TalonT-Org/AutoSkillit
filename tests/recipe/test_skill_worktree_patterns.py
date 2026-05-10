"""Tests that SKILL.md files do not use fragile relative worktree path patterns.

Architectural guard: any future SKILL.md that introduces '../worktrees/' in a
bash code block context will fail these tests immediately, preventing regression
without requiring developer awareness of this specific bug.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core import pkg_root

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _skill_md_paths() -> list[tuple[str, Path]]:
    """Return list of (skill_name, SKILL.md_path) for skills_extended skills."""
    skills_extended = pkg_root() / "skills_extended"
    paths = []
    for skill_dir in sorted(skills_extended.iterdir()):
        if skill_dir.is_dir():
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                paths.append((skill_dir.name, skill_md))
    return paths


# ---------------------------------------------------------------------------
# Absolute path resolution pattern tests
# ---------------------------------------------------------------------------


class TestImplementWorktreeSkillAbsoluteResolution:
    def test_implement_worktree_skill_uses_absolute_resolution(self) -> None:
        """implement-worktree SKILL.md uses absolute resolution, not '../worktrees/'."""
        skill_md = pkg_root() / "skills_extended" / "implement-worktree" / "SKILL.md"
        text = skill_md.read_text()

        # Must not contain the fragile relative path pattern in bash code blocks
        bash_blocks = re.findall(r"```bash(.*?)```", text, re.DOTALL)
        for block in bash_blocks:
            if "../worktrees/" in block:
                pytest.fail(
                    "implement-worktree/SKILL.md contains fragile '../worktrees/' pattern "
                    "in a bash code block. Use absolute resolution via "
                    "'git rev-parse --path-format=absolute --git-common-dir' instead."
                )

        # Must contain the absolute resolution approach
        assert "--path-format=absolute --git-common-dir" in text, (
            "implement-worktree/SKILL.md must use "
            "'git rev-parse --path-format=absolute --git-common-dir' for worktree creation"
        )

    def test_implement_worktree_no_merge_skill_uses_absolute_resolution(self) -> None:
        """implement-worktree-no-merge SKILL.md uses absolute resolution."""
        skill_md = pkg_root() / "skills_extended" / "implement-worktree-no-merge" / "SKILL.md"
        text = skill_md.read_text()

        bash_blocks = re.findall(r"```bash(.*?)```", text, re.DOTALL)
        for block in bash_blocks:
            if "../worktrees/" in block:
                pytest.fail(
                    "implement-worktree-no-merge/SKILL.md contains fragile '../worktrees/' "
                    "pattern in a bash code block. Use absolute resolution via "
                    "'git rev-parse --path-format=absolute --git-common-dir' instead."
                )

        assert "--path-format=absolute --git-common-dir" in text, (
            "implement-worktree-no-merge/SKILL.md must use "
            "'git rev-parse --path-format=absolute --git-common-dir' for worktree creation"
        )

    def test_implement_experiment_skill_uses_absolute_resolution(self) -> None:
        """implement-experiment SKILL.md uses absolute resolution."""
        skill_md = pkg_root() / "skills_extended" / "implement-experiment" / "SKILL.md"
        text = skill_md.read_text()

        bash_blocks = re.findall(r"```bash(.*?)```", text, re.DOTALL)
        for block in bash_blocks:
            if "../worktrees/" in block:
                pytest.fail(
                    "implement-experiment/SKILL.md contains fragile '../worktrees/' pattern "
                    "in a bash code block. Use absolute resolution via "
                    "'git rev-parse --path-format=absolute --git-common-dir' instead."
                )

        assert "--path-format=absolute --git-common-dir" in text, (
            "implement-experiment/SKILL.md must use "
            "'git rev-parse --path-format=absolute --git-common-dir' for worktree creation"
        )


class TestNoSkillMdUsesRelativeWorktreePath:
    """Sweep test: no SKILL.md in skills_extended/ may use the relative pattern."""

    @pytest.mark.parametrize(
        "skill_name,skill_md_path",
        _skill_md_paths(),
        ids=[name for name, _ in _skill_md_paths()],
    )
    def test_no_skill_md_uses_relative_worktree_path_in_bash_blocks(
        self, skill_name: str, skill_md_path: Path
    ) -> None:
        """SKILL.md files must not contain '../worktrees/' in bash code blocks.

        This is the permanent architectural guard: any future skill using the
        relative pattern will fail this test immediately.
        """
        text = skill_md_path.read_text()
        bash_blocks = re.findall(r"```bash(.*?)```", text, re.DOTALL)

        for block in bash_blocks:
            if "../worktrees/" in block:
                pytest.fail(
                    f"{skill_name}/SKILL.md contains fragile '../worktrees/' pattern "
                    f"in a bash code block. Worktree creation must use absolute path resolution "
                    f"via 'git rev-parse --path-format=absolute --git-common-dir'."
                )

    @pytest.mark.parametrize(
        "skill_name,skill_md_path",
        _skill_md_paths(),
        ids=[name for name, _ in _skill_md_paths()],
    )
    def test_no_worktree_creating_skill_uses_relative_path(
        self, skill_name: str, skill_md_path: Path
    ) -> None:
        """Skills that create worktrees must not use relative '../worktrees/' paths."""
        text = skill_md_path.read_text()
        creates_worktree = "git worktree add" in text
        if not creates_worktree:
            return  # Not a worktree-creating skill, skip

        # If it creates worktrees, it must not use the relative path pattern
        bash_blocks = re.findall(r"```bash(.*?)```", text, re.DOTALL)
        for block in bash_blocks:
            if "git worktree add" in block and "../worktrees/" in block:
                pytest.fail(
                    f"{skill_name}/SKILL.md creates worktrees but uses fragile "
                    f"relative '../worktrees/' path. Use absolute resolution via "
                    f"'git rev-parse --path-format=absolute --git-common-dir'."
                )


class TestRetryWorktreeSkillPathFormat:
    def test_retry_worktree_uses_absolute_path_format(self) -> None:
        """retry-worktree SKILL.md uses --path-format=absolute for git-common-dir."""
        skill_md = pkg_root() / "skills_extended" / "retry-worktree" / "SKILL.md"
        text = skill_md.read_text()

        # The retry-worktree fallback uses git-common-dir; it should use
        # --path-format=absolute to ensure consistent resolution
        if "git-common-dir" in text:
            assert "--path-format=absolute --git-common-dir" in text, (
                "retry-worktree/SKILL.md uses git-common-dir; it must use "
                "'--path-format=absolute --git-common-dir' for reliable resolution"
            )
