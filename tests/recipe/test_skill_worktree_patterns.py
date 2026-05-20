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


_SKILL_MD_PATHS = _skill_md_paths()


# ---------------------------------------------------------------------------
# Absolute path resolution pattern tests
# ---------------------------------------------------------------------------


class TestImplementWorktreeSkillAbsoluteResolution:
    """Step 1 bash blocks delegate to create_impl_worktree.sh; the script owns the
    --path-format=absolute --git-common-dir resolution internally."""

    def _step1_bash_block(self, skill_md_path: Path) -> str:
        """Extract the Step 1 bash block from a SKILL.md."""
        text = skill_md_path.read_text()
        # Match the Step 1 bash block (first ```bash block after "Step 1")
        matches = re.findall(r"### Step 1.*?```bash(.*?)```", text, re.DOTALL)
        if not matches:
            pytest.fail(f"Could not find Step 1 bash block in {skill_md_path}")
        return matches[0]

    def test_implement_worktree_skill_uses_absolute_resolution(self) -> None:
        """implement-worktree Step 1 bash block delegates to create_impl_worktree.sh."""
        skill_md = pkg_root() / "skills_extended" / "implement-worktree" / "SKILL.md"
        block = self._step1_bash_block(skill_md)

        # Must not contain the fragile relative path pattern
        if "../worktrees/" in block:
            pytest.fail(
                "implement-worktree/SKILL.md Step 1 contains fragile '../worktrees/' pattern. "
                "Worktree creation must use create_impl_worktree.sh."
            )

        # Must invoke the shared script; the script owns --path-format=absolute internally
        assert "create_impl_worktree.sh" in block, (
            "implement-worktree/SKILL.md Step 1 must invoke create_impl_worktree.sh"
        )
        # Must NOT contain the old 4-variable chain directly in the SKILL.md bash block
        assert "MAIN_GIT_DIR=" not in block, (
            "implement-worktree/SKILL.md Step 1 must not contain MAIN_GIT_DIR= assignment; "
            "that is handled inside create_impl_worktree.sh"
        )
        # The --path-format=absolute flag is in the script, not the SKILL.md step 1 block
        assert "--path-format=absolute --git-common-dir" not in block, (
            "implement-worktree/SKILL.md Step 1 must not contain "
            "--path-format=absolute --git-common-dir directly; "
            "that flag is used inside create_impl_worktree.sh"
        )

    def test_implement_worktree_no_merge_skill_uses_absolute_resolution(self) -> None:
        """implement-worktree-no-merge Step 1 bash block delegates to create_impl_worktree.sh."""
        skill_md = pkg_root() / "skills_extended" / "implement-worktree-no-merge" / "SKILL.md"
        block = self._step1_bash_block(skill_md)

        if "../worktrees/" in block:
            pytest.fail(
                "implement-worktree-no-merge/SKILL.md Step 1 contains "
                "fragile '../worktrees/' pattern. "
                "Worktree creation must use create_impl_worktree.sh."
            )

        assert "create_impl_worktree.sh" in block, (
            "implement-worktree-no-merge/SKILL.md Step 1 must invoke create_impl_worktree.sh"
        )
        assert "MAIN_GIT_DIR=" not in block, (
            "implement-worktree-no-merge/SKILL.md Step 1 must not contain "
            "MAIN_GIT_DIR= assignment; "
            "that is handled inside create_impl_worktree.sh"
        )
        assert "--path-format=absolute --git-common-dir" not in block, (
            "implement-worktree-no-merge/SKILL.md Step 1 must not contain "
            "--path-format=absolute --git-common-dir directly; "
            "that flag is used inside create_impl_worktree.sh"
        )

    def test_implement_experiment_skill_uses_absolute_resolution(self) -> None:
        """implement-experiment Step 1 bash block delegates to create_impl_worktree.sh."""
        skill_md = pkg_root() / "skills_extended" / "implement-experiment" / "SKILL.md"
        block = self._step1_bash_block(skill_md)

        if "../worktrees/" in block:
            pytest.fail(
                "implement-experiment/SKILL.md Step 1 contains fragile '../worktrees/' pattern. "
                "Worktree creation must use create_impl_worktree.sh."
            )

        assert "create_impl_worktree.sh" in block, (
            "implement-experiment/SKILL.md Step 1 must invoke create_impl_worktree.sh"
        )
        assert "MAIN_GIT_DIR=" not in block, (
            "implement-experiment/SKILL.md Step 1 must not contain MAIN_GIT_DIR= assignment; "
            "that is handled inside create_impl_worktree.sh"
        )
        assert "--path-format=absolute --git-common-dir" not in block, (
            "implement-experiment/SKILL.md Step 1 must not contain "
            "--path-format=absolute --git-common-dir directly; "
            "that flag is used inside create_impl_worktree.sh"
        )


class TestImplementSkillsInvokeSharedScript:
    """T12: Each implement-* skill's Step 1 bash block invokes create_impl_worktree.sh
    and does not contain the old 4-variable chain pattern (MAIN_GIT_DIR= assignment
    within the bash block)."""

    _SKILLS = (
        "implement-worktree",
        "implement-worktree-no-merge",
        "implement-experiment",
    )

    @pytest.mark.parametrize("skill_name", _SKILLS, ids=_SKILLS)
    def test_step1_invokes_create_impl_worktree_sh(self, skill_name: str) -> None:
        """Step 1 bash block must invoke create_impl_worktree.sh."""
        skill_md = pkg_root() / "skills_extended" / skill_name / "SKILL.md"
        text = skill_md.read_text()
        bash_blocks = re.findall(r"```bash(.*?)```", text, re.DOTALL)
        step1_blocks = [
            b for b in bash_blocks if "create_impl_worktree" in b or "WORKTREE_NAME=" in b
        ]
        assert step1_blocks, (
            f"{skill_name}/SKILL.md Step 1 bash block not found or does not reference "
            "worktree creation"
        )
        assert any("create_impl_worktree.sh" in b for b in step1_blocks), (
            f"{skill_name}/SKILL.md Step 1 bash block must invoke create_impl_worktree.sh"
        )

    @pytest.mark.parametrize("skill_name", _SKILLS, ids=_SKILLS)
    def test_step1_does_not_contain_old_4variable_chain(self, skill_name: str) -> None:
        """Step 1 bash block must NOT contain the old MAIN_GIT_DIR= assignment pattern."""
        skill_md = pkg_root() / "skills_extended" / skill_name / "SKILL.md"
        text = skill_md.read_text()
        bash_blocks = re.findall(r"```bash(.*?)```", text, re.DOTALL)
        for block in bash_blocks:
            if "WORKTREE_NAME=" in block or "create_impl_worktree" in block:
                assert "MAIN_GIT_DIR=" not in block, (
                    f"{skill_name}/SKILL.md Step 1 bash block must NOT contain "
                    "the old MAIN_GIT_DIR= variable assignment; "
                    "that chain is now encapsulated inside create_impl_worktree.sh"
                )


class TestNoSkillMdUsesRelativeWorktreePath:
    """Sweep test: no SKILL.md in skills_extended/ may use the relative pattern."""

    @pytest.mark.parametrize(
        "skill_name,skill_md_path",
        _SKILL_MD_PATHS,
        ids=[name for name, _ in _SKILL_MD_PATHS],
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
        _SKILL_MD_PATHS,
        ids=[name for name, _ in _SKILL_MD_PATHS],
    )
    def test_no_worktree_creating_skill_uses_relative_path(
        self, skill_name: str, skill_md_path: Path
    ) -> None:
        """Skills that create worktrees must not use relative '../worktrees/' paths."""
        text = skill_md_path.read_text()
        creates_worktree = "git worktree add" in text
        if not creates_worktree:
            pytest.skip("not a worktree-creating skill")

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
